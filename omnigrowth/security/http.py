"""SSRF-safe HTTP fetcher for user-supplied URLs (spec §25, §26; ARCHITECTURE §3 rule 4).

This is the ONLY code allowed to fetch user-supplied URLs. Defences:
1. Scheme allowlist (http/https); no credentials in URLs; port allowlist (80/443 default).
2. Obfuscated IPv4 literals (decimal ``2130706433``, hex ``0x7f000001``, octal ``0177.0.0.1``,
   short ``127.1``) are rejected outright: the OS resolver would otherwise accept them.
3. The host is resolved ONCE; every resolved address must be globally routable (blocks
   loopback, RFC1918, link-local/metadata, CGNAT, ULA, multicast, reserved, unspecified,
   IPv4-mapped / NAT64 / 6to4 addresses that embed a blocked IPv4).
4. The connection goes to the pinned IP (Host header + TLS SNI keep the original name, so
   certificate validation still happens against it) — defeats DNS rebinding between check
   and connect.
5. Redirects are followed manually (max 5) and every hop is re-validated from step 1.
6. Response size is capped while streaming; content-type allowlist; total timeout.
No proxies (trust_env=False), no cookies, no auth headers are ever sent.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from omnigrowth.core.errors import ErrorCode, OmniGrowthError

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[IPAddress]]]

DEFAULT_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "text/markdown",
        "application/pdf",
    }
)

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
_SIXTO4 = ipaddress.ip_network("2002::/16")
# Hostnames that look like numbers in any base / partial dotted form (inet_aton accepts them).
_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    allowed_ports: frozenset[int] = frozenset({80, 443})
    max_redirects: int = 5
    max_bytes: int = 10 * 1024 * 1024  # API_PLAN: URL ingestion 10 MiB
    connect_timeout_s: float = 10.0  # API_PLAN section 6.2
    total_timeout_s: float = 30.0  # whole fetch incl. DNS, redirects and body
    allowed_content_types: frozenset[str] = DEFAULT_ALLOWED_CONTENT_TYPES
    user_agent: str = "OmniGrowth-Ingest/0.1 (+local)"


@dataclass(frozen=True, slots=True)
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    resolved_ip: str
    redirects: list[str] = field(default_factory=list)


def _blocked(reason: str, url: str, **details: object) -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.SSRF_BLOCKED,
        f"URL not allowed: {reason}",
        details={"reason": reason, "url": url, **details},
    )


# ------------------------------------------------------------------ address policy
def embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """IPv4 carried inside an IPv6 address (mapped, NAT64, 6to4, compatible)."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64 or ip in _NAT64_LOCAL:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.sixtofour is not None:
        return ip.sixtofour
    if int(ip) >> 32 == 0 and int(ip) > 1:  # deprecated IPv4-compatible ::a.b.c.d
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_blocked_ip(ip: IPAddress) -> bool:
    """True unless ``ip`` is a globally routable unicast address."""
    if isinstance(ip, ipaddress.IPv6Address):
        inner = embedded_ipv4(ip)
        if inner is not None and is_blocked_ip(inner):
            return True
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


# ------------------------------------------------------------------ URL policy
@dataclass(frozen=True, slots=True)
class _Target:
    url: str
    scheme: str
    host: str  # IDNA-encoded, lowercase, no brackets
    port: int
    path_and_query: str


def parse_target(url: str, policy: FetchPolicy) -> _Target:
    if len(url) > 2048:
        raise _blocked("url too long", url[:100])
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise _blocked("malformed url", url) from exc
    scheme = parts.scheme.lower()
    if scheme not in policy.allowed_schemes:
        raise _blocked("scheme not allowed", url, scheme=scheme)
    if parts.username is not None or parts.password is not None:
        raise _blocked("credentials in url", url)
    raw_host = parts.hostname
    if not raw_host:
        raise _blocked("missing host", url)
    if "%" in raw_host or ".." in raw_host or raw_host.startswith("."):
        raise _blocked("invalid host", url)
    try:
        host = raw_host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise _blocked("invalid host", url) from exc
    port = port or (443 if scheme == "https" else 80)
    if port not in policy.allowed_ports:
        raise _blocked("port not allowed", url, port=port)
    if _NUMERIC_HOST.match(host):
        # Canonical dotted-quad is handled as an IP literal below; everything else numeric
        # (decimal, hex, octal, short forms) is an obfuscation attempt.
        try:
            ipaddress.IPv4Address(host)
        except ipaddress.AddressValueError as exc:
            raise _blocked("obfuscated ip literal", url, host=host) from exc
    path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    return _Target(url=url, scheme=scheme, host=host, port=port, path_and_query=path)


async def system_resolver(host: str, port: int) -> list[IPAddress]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise OmniGrowthError(
            ErrorCode.INGESTION_FAILURE,
            "host could not be resolved",
            details={"reason": "DNS_FAILURE", "host": host},
        ) from exc
    addresses: list[IPAddress] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        addresses.append(ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0]))
    return addresses


async def resolve_and_check(target: _Target, resolver: Resolver) -> IPAddress:
    try:
        literal: IPAddress | None = ipaddress.ip_address(target.host)
    except ValueError:
        literal = None
    addresses = [literal] if literal is not None else await resolver(target.host, target.port)
    if not addresses:
        raise _blocked("host resolved to no addresses", target.url)
    for ip in addresses:
        # ALL answers must be public: a mixed answer set is a rebinding / split-horizon risk.
        if is_blocked_ip(ip):
            raise _blocked("destination address not allowed", target.url, address=str(ip))
    return addresses[0]


def _pinned_url(target: _Target, ip: IPAddress) -> str:
    host = f"[{ip}]" if isinstance(ip, ipaddress.IPv6Address) else str(ip)
    default = 443 if target.scheme == "https" else 80
    netloc = host if target.port == default else f"{host}:{target.port}"
    return f"{target.scheme}://{netloc}{target.path_and_query}"


def _host_header(target: _Target) -> str:
    default = 443 if target.scheme == "https" else 80
    return target.host if target.port == default else f"{target.host}:{target.port}"


# ------------------------------------------------------------------ fetch
async def safe_fetch(
    url: str,
    *,
    policy: FetchPolicy | None = None,
    resolver: Resolver = system_resolver,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchResult:
    policy = policy or FetchPolicy()
    try:
        async with asyncio.timeout(policy.total_timeout_s):
            return await _fetch(url, policy, resolver, transport)
    except TimeoutError as exc:
        raise OmniGrowthError(
            ErrorCode.INGESTION_FAILURE,
            "fetch timed out",
            details={"reason": "TIMEOUT", "url": url},
        ) from exc


def _transport_error(exc: httpx.HTTPError, url: str) -> OmniGrowthError:
    reason = "TIMEOUT" if isinstance(exc, httpx.TimeoutException) else type(exc).__name__
    return OmniGrowthError(
        ErrorCode.INGESTION_FAILURE,
        "fetch timed out" if reason == "TIMEOUT" else "fetch failed",
        details={"reason": reason, "url": url},
    )


async def _fetch(
    url: str,
    policy: FetchPolicy,
    resolver: Resolver,
    transport: httpx.AsyncBaseTransport | None,
) -> FetchResult:
    redirects: list[str] = []
    current = url
    timeout = httpx.Timeout(policy.total_timeout_s, connect=policy.connect_timeout_s)
    for _hop in range(policy.max_redirects + 1):
        target = parse_target(current, policy)
        ip = await resolve_and_check(target, resolver)
        # A fresh client per hop: connection pools key on the pinned IP only, so reusing one
        # across hops could reuse a TLS session verified for a different hostname.
        async with httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,  # never route through system/env proxies
        ) as client:
            try:
                request = client.build_request(
                    "GET",
                    _pinned_url(target, ip),
                    headers={
                        "Host": _host_header(target),
                        "User-Agent": policy.user_agent,
                        "Accept": ", ".join(sorted(policy.allowed_content_types)),
                        "Connection": "close",
                    },
                    extensions={"sni_hostname": target.host} if target.scheme == "https" else {},
                )
                response = await client.send(request, stream=True)
            except httpx.InvalidURL as exc:
                raise _blocked("invalid url", current) from exc
            except httpx.HTTPError as exc:
                raise _transport_error(exc, current) from exc
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise OmniGrowthError(
                            ErrorCode.INGESTION_FAILURE,
                            "redirect without location",
                            details={"reason": "BAD_REDIRECT", "url": current},
                        )
                    redirects.append(current)
                    current = urljoin(current, location)
                    continue
                try:
                    return await _read_body(response, target, ip, url, redirects, policy)
                except httpx.HTTPError as exc:  # stalls, resets, bad gzip while streaming
                    raise _transport_error(exc, current) from exc
            finally:
                await response.aclose()
    raise _blocked("too many redirects", url, redirects=len(redirects))


async def _read_body(
    response: httpx.Response,
    target: _Target,
    ip: IPAddress,
    requested: str,
    redirects: list[str],
    policy: FetchPolicy,
) -> FetchResult:
    if response.status_code != 200:
        raise OmniGrowthError(
            ErrorCode.INGESTION_FAILURE,
            f"fetch returned HTTP {response.status_code}",
            details={"reason": "HTTP_STATUS", "status": response.status_code},
        )
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in policy.allowed_content_types:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "content type not allowed",
            details={"reason": "CONTENT_TYPE", "content_type": content_type or None},
        )
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > policy.max_bytes:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "response too large",
            details={"reason": "TOO_LARGE", "limit": policy.max_bytes},
        )
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > policy.max_bytes:
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE,
                "response too large",
                details={"reason": "TOO_LARGE", "limit": policy.max_bytes},
            )
        chunks.append(chunk)
    return FetchResult(
        requested_url=requested,
        final_url=target.url,
        status_code=response.status_code,
        content_type=content_type,
        body=b"".join(chunks),
        resolved_ip=str(ip),
        redirects=list(redirects),
    )


def precheck_url(url: str, policy: FetchPolicy | None = None) -> None:
    """Synchronous pre-check for request handlers (API_PLAN sources/url): scheme, port,
    credentials, obfuscated literals and blocked IP literals. No DNS; the fetch itself
    repeats the full guard (resolve, pin, re-validate every redirect)."""
    target = parse_target(url, policy or FetchPolicy())
    try:
        literal = ipaddress.ip_address(target.host)
    except ValueError:
        return
    if is_blocked_ip(literal):
        raise _blocked("destination address not allowed", url, address=str(literal))


# ------------------------------------------------------------------ outbound webhooks (Phase 12)
ALL_PORTS = frozenset(range(1, 65536))


def _allowlisted(target: _Target, allowlist: frozenset[str]) -> bool:
    return f"{target.host}:{target.port}" in allowlist


def _never_allowed(ip: IPAddress) -> bool:
    """Blocked even for allowlisted hosts: link-local (cloud metadata), unspecified, multicast."""
    if isinstance(ip, ipaddress.IPv6Address) and (inner := embedded_ipv4(ip)) is not None:
        return _never_allowed(inner)
    return ip.is_link_local or ip.is_unspecified or ip.is_multicast


def check_webhook_target(url: str, allowlist: frozenset[str]) -> None:
    """Validate a webhook target when it is configured (no DNS; ``safe_post`` re-checks).

    Public targets: HTTPS on 443 only. ``host:port`` entries in ``allowlist`` (e.g. a
    self-hosted n8n) may use HTTP and private addresses on exactly that port.
    """
    target = parse_target(url, FetchPolicy(allowed_ports=ALL_PORTS))
    if urlsplit(url).fragment:
        raise _blocked("fragment not allowed", url)
    if _allowlisted(target, allowlist):
        return
    if target.scheme != "https" or target.port != 443:
        raise _blocked("public webhook targets must use https on port 443", url)
    precheck_url(url)


async def safe_post(
    url: str,
    *,
    body: bytes,
    headers: dict[str, str],
    allowlist: frozenset[str],
    timeout_s: float,
    resolver: Resolver = system_resolver,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """POST a signed webhook and return the HTTP status (the body is discarded).

    Same guard as fetches: parsed and resolved once, every address checked, connection
    pinned to the checked IP (DNS rebinding), no proxies, no cookies, no redirects followed
    (a 3xx is returned as the status and counts as a failed attempt).
    """
    check_webhook_target(url, allowlist)
    target = parse_target(url, FetchPolicy(allowed_ports=ALL_PORTS))
    trusted = _allowlisted(target, allowlist)
    try:
        literal: IPAddress | None = ipaddress.ip_address(target.host)
    except ValueError:
        literal = None
    try:
        async with asyncio.timeout(timeout_s):
            addresses = (
                [literal] if literal is not None else await resolver(target.host, target.port)
            )
            if not addresses:
                raise _blocked("host resolved to no addresses", url)
            for ip in addresses:
                if _never_allowed(ip) or (not trusted and is_blocked_ip(ip)):
                    raise _blocked("destination address not allowed", url, address=str(ip))
            ip = addresses[0]
            async with httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(timeout_s),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                request = client.build_request(
                    "POST",
                    _pinned_url(target, ip),
                    content=body,
                    headers={
                        **headers,
                        "Host": _host_header(target),
                        "User-Agent": "OmniGrowth-Webhooks/1",
                        "Content-Type": "application/json",
                        "Connection": "close",
                    },
                    extensions={"sni_hostname": target.host} if target.scheme == "https" else {},
                )
                response = await client.send(request, stream=True)
                await response.aclose()
                return response.status_code
    except TimeoutError as exc:
        raise OmniGrowthError(
            ErrorCode.QUEUE_FAILURE, "webhook timed out", details={"reason": "TIMEOUT"}
        ) from exc
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise OmniGrowthError(
            ErrorCode.QUEUE_FAILURE,
            "webhook request failed",
            details={"reason": type(exc).__name__},
        ) from exc
