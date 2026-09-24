"""SSRF guard test matrix (spec §26; PHASES.md Phase 4 gate: >=15 cases incl. redirect-to-private,
IPv6 ``::ffff:127.0.0.1``, decimal-encoded IP, DNS returning a private IP)."""

from __future__ import annotations

import ipaddress

import httpx
import pytest

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.security.http import FetchPolicy, IPAddress, safe_fetch

PUBLIC_IP = "93.184.215.14"


def resolver_for(mapping: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[IPAddress]:
        if host not in mapping:
            raise AssertionError(f"unexpected DNS lookup for {host}")
        return [ipaddress.ip_address(a) for a in mapping[host]]

    return resolve


def html_transport(
    routes: dict[str, httpx.Response] | None = None, seen: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        key = f"{request.headers['host']}{request.url.raw_path.decode()}"
        if routes and key in routes:
            return routes[key]
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text="<h1>ok</h1>"
        )

    return httpx.MockTransport(handler)


async def expect_blocked(url: str, dns: dict[str, list[str]] | None = None) -> OmniGrowthError:
    with pytest.raises(OmniGrowthError) as exc:
        await safe_fetch(url, resolver=resolver_for(dns or {}), transport=html_transport())
    assert exc.value.code is ErrorCode.SSRF_BLOCKED, exc.value
    return exc.value


# ------------------------------------------------------------------ blocked literals / schemes
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",  # loopback
        "http://10.0.0.5/",  # RFC1918
        "http://172.16.3.4/",  # RFC1918
        "http://192.168.1.1/",  # RFC1918
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://100.64.0.1/",  # CGNAT
        "http://0.0.0.0/",  # unspecified
        "http://[::1]/",  # IPv6 loopback
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped IPv6 loopback
        "http://[fd00:ec2::254]/",  # ULA (AWS IPv6 metadata)
        "http://[fe80::1]/",  # IPv6 link-local
        "http://[64:ff9b::7f00:1]/",  # NAT64 embedding 127.0.0.1
        "http://[2002:7f00:1::]/",  # 6to4 embedding 127.0.0.1
        "http://192.0.2.10/",  # TEST-NET (reserved documentation range)
        "http://224.0.0.1/",  # multicast
    ],
)
async def test_blocked_ip_literals(url: str) -> None:
    error = await expect_blocked(url)
    assert error.details["reason"] == "destination address not allowed"


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",  # decimal 127.0.0.1
        "http://0x7f000001/",  # hex
        "http://0177.0.0.1/",  # octal
        "http://127.1/",  # short form
        "http://0x7f.0.0.1/",  # mixed
    ],
)
async def test_obfuscated_ip_literals_are_rejected_before_dns(url: str) -> None:
    error = await expect_blocked(url)
    assert error.details["reason"] == "obfuscated ip literal"


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("file:///etc/passwd", "scheme not allowed"),
        ("ftp://example.com/x", "scheme not allowed"),
        ("gopher://example.com/", "scheme not allowed"),
        ("javascript:alert(1)", "scheme not allowed"),
        ("http://user:pass@example.com/", "credentials in url"),
        ("http://example.com:22/", "port not allowed"),
        ("http://example.com:6379/", "port not allowed"),
        ("http:///nohost", "missing host"),
        ("http://[2001:4860:4860::8888%25eth0]/", "invalid host"),  # IPv6 zone id
        ("http://a..example/", "invalid host"),  # empty label
    ],
)
async def test_scheme_port_credentials_policy(url: str, reason: str) -> None:
    error = await expect_blocked(url)
    assert error.details["reason"] == reason


# ------------------------------------------------------------------ DNS-based attacks
async def test_hostname_resolving_to_private_ip_is_blocked() -> None:
    await expect_blocked("http://evil.example/", {"evil.example": ["10.1.2.3"]})


async def test_localhost_name_is_blocked_via_resolution() -> None:
    await expect_blocked("http://localhost/", {"localhost": ["127.0.0.1", "::1"]})


async def test_metadata_hostname_is_blocked() -> None:
    await expect_blocked(
        "http://metadata.google.internal/computeMetadata/v1/",
        {"metadata.google.internal": ["169.254.169.254"]},
    )


async def test_mixed_public_and_private_answers_are_blocked() -> None:
    """Split-horizon / rebinding: every answer must be public, not just the first."""
    await expect_blocked("http://mixed.example/", {"mixed.example": [PUBLIC_IP, "192.168.0.10"]})


async def test_connection_is_pinned_to_the_checked_ip() -> None:
    """DNS rebinding defence: we connect to the IP we validated, with the original Host/SNI."""
    seen: list[httpx.Request] = []
    lookups: list[str] = []

    async def resolver(host: str, port: int) -> list[IPAddress]:
        lookups.append(host)
        return [ipaddress.ip_address(PUBLIC_IP)]

    result = await safe_fetch(
        "https://news.example/article?id=7", resolver=resolver, transport=html_transport(seen=seen)
    )
    assert lookups == ["news.example"]  # resolved exactly once
    request = seen[0]
    assert request.url.host == PUBLIC_IP
    assert request.headers["host"] == "news.example"
    assert request.extensions["sni_hostname"] == "news.example"
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert result.resolved_ip == PUBLIC_IP
    assert result.body == b"<h1>ok</h1>"


async def test_idn_hostname_is_idna_encoded() -> None:
    seen: list[httpx.Request] = []
    await safe_fetch(
        "http://bücher.example/",
        resolver=resolver_for({"xn--bcher-kva.example": [PUBLIC_IP]}),
        transport=html_transport(seen=seen),
    )
    assert seen[0].headers["host"] == "xn--bcher-kva.example"


# ------------------------------------------------------------------ redirects
async def test_redirect_to_private_ip_is_blocked() -> None:
    routes = {
        "public.example/": httpx.Response(302, headers={"location": "http://127.0.0.1/admin"}),
    }
    with pytest.raises(OmniGrowthError) as exc:
        await safe_fetch(
            "http://public.example/",
            resolver=resolver_for({"public.example": [PUBLIC_IP]}),
            transport=html_transport(routes),
        )
    assert exc.value.code is ErrorCode.SSRF_BLOCKED


async def test_redirect_to_hostname_resolving_private_is_blocked() -> None:
    routes = {"a.example/": httpx.Response(301, headers={"location": "http://b.example/x"})}
    with pytest.raises(OmniGrowthError) as exc:
        await safe_fetch(
            "http://a.example/",
            resolver=resolver_for({"a.example": [PUBLIC_IP], "b.example": ["10.0.0.9"]}),
            transport=html_transport(routes),
        )
    assert exc.value.code is ErrorCode.SSRF_BLOCKED


async def test_redirect_to_file_scheme_is_blocked() -> None:
    routes = {"a.example/": httpx.Response(302, headers={"location": "file:///etc/passwd"})}
    with pytest.raises(OmniGrowthError) as exc:
        await safe_fetch(
            "http://a.example/",
            resolver=resolver_for({"a.example": [PUBLIC_IP]}),
            transport=html_transport(routes),
        )
    assert exc.value.details["reason"] == "scheme not allowed"


async def test_relative_redirect_is_followed_and_recorded() -> None:
    routes = {"a.example/old": httpx.Response(301, headers={"location": "/new"})}
    result = await safe_fetch(
        "http://a.example/old",
        resolver=resolver_for({"a.example": [PUBLIC_IP]}),
        transport=html_transport(routes),
    )
    assert result.final_url == "http://a.example/new"
    assert result.redirects == ["http://a.example/old"]


async def test_too_many_redirects() -> None:
    routes = {
        f"a.example/{i}": httpx.Response(302, headers={"location": f"/{i + 1}"}) for i in range(10)
    }
    with pytest.raises(OmniGrowthError) as exc:
        await safe_fetch(
            "http://a.example/0",
            resolver=resolver_for({"a.example": [PUBLIC_IP]}),
            transport=html_transport(routes),
        )
    assert exc.value.details["reason"] == "too many redirects"


# ------------------------------------------------------------------ response limits
async def test_body_over_limit_is_rejected_while_streaming() -> None:
    routes = {
        "big.example/": httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"x" * 2048
        )
    }
    with pytest.raises(OmniGrowthError) as exc:
        await safe_fetch(
            "http://big.example/",
            policy=FetchPolicy(max_bytes=1024),
            resolver=resolver_for({"big.example": [PUBLIC_IP]}),
            transport=html_transport(routes),
        )
    assert exc.value.details["reason"] == "TOO_LARGE"


async def test_disallowed_content_type_is_rejected() -> None:
    routes = {
        "bin.example/": httpx.Response(
            200, headers={"content-type": "application/x-msdownload"}, content=b"MZ"
        )
    }
    with pytest.raises(OmniGrowthError) as exc:
        await safe_fetch(
            "http://bin.example/",
            resolver=resolver_for({"bin.example": [PUBLIC_IP]}),
            transport=html_transport(routes),
        )
    assert exc.value.code is ErrorCode.VALIDATION_FAILURE
    assert exc.value.details["reason"] == "CONTENT_TYPE"


async def test_non_200_is_ingestion_failure() -> None:
    routes = {"gone.example/": httpx.Response(404, headers={"content-type": "text/html"})}
    with pytest.raises(OmniGrowthError) as exc:
        await safe_fetch(
            "http://gone.example/",
            resolver=resolver_for({"gone.example": [PUBLIC_IP]}),
            transport=html_transport(routes),
        )
    assert exc.value.code is ErrorCode.INGESTION_FAILURE


async def test_network_timeout_is_ingestion_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(OmniGrowthError) as exc:
        await safe_fetch(
            "http://slow.example/",
            resolver=resolver_for({"slow.example": [PUBLIC_IP]}),
            transport=httpx.MockTransport(handler),
        )
    assert exc.value.details["reason"] == "TIMEOUT"


async def test_stall_while_streaming_body_is_ingestion_failure() -> None:
    class StallStream(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def,override]
            yield b"<h1>partial"
            raise httpx.ReadTimeout("stalled")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=StallStream())

    with pytest.raises(OmniGrowthError) as exc:
        await safe_fetch(
            "http://slow.example/",
            resolver=resolver_for({"slow.example": [PUBLIC_IP]}),
            transport=httpx.MockTransport(handler),
        )
    assert exc.value.code is ErrorCode.INGESTION_FAILURE
    assert exc.value.details["reason"] == "TIMEOUT"


async def test_total_deadline_covers_slow_dns() -> None:
    import asyncio

    async def slow_resolver(host: str, port: int) -> list[IPAddress]:
        await asyncio.sleep(5)
        return [ipaddress.ip_address(PUBLIC_IP)]

    with pytest.raises(OmniGrowthError) as exc:
        await safe_fetch(
            "http://slow-dns.example/",
            policy=FetchPolicy(total_timeout_s=0.05),
            resolver=slow_resolver,
            transport=html_transport(),
        )
    assert exc.value.details["reason"] == "TIMEOUT"
