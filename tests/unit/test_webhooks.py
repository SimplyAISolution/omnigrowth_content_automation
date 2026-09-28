"""Phase 12 unit tests: webhook signatures, derived secrets, target validation, the pinned
outbound POST, backoff and payload minimality."""

from __future__ import annotations

import ipaddress
import itertools
import json
import random
import time
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.settings import get_settings
from omnigrowth.models.integrations import IntegrationEvent
from omnigrowth.security import webhooks as sig
from omnigrowth.security.http import check_webhook_target, safe_post
from omnigrowth.services import webhook_delivery as wd

KEY = b"k" * 32


# ------------------------------------------------------------------ secrets + signatures
def test_secrets_are_derived_per_row_and_version_and_need_a_server_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = uuid.uuid4()
    s1 = sig.derive_secret("endpoint", row, 1, KEY)
    assert s1 == sig.derive_secret("endpoint", row, 1, KEY)
    assert s1 != sig.derive_secret("endpoint", row, 2, KEY)  # rotation
    assert s1 != sig.derive_secret("subscription", row, 1, KEY)  # kind separation
    assert s1 != sig.derive_secret("endpoint", uuid.uuid4(), 1, KEY)
    assert s1.startswith("whsec_") and len(s1) > 40
    monkeypatch.setenv("OMNIGROWTH_WEBHOOK_KEY", "too-short")
    get_settings.cache_clear()
    with pytest.raises(OmniGrowthError) as err:
        sig.server_key()
    assert err.value.code is ErrorCode.CONFIGURATION_ERROR


def test_signature_verification_rejects_tampering_staleness_and_missing_headers() -> None:
    secret, old = "whsec_current", "whsec_previous"
    body = b'{"operation":"source.text_ingest"}'
    now = time.time()
    ts = int(now)
    dlv = "dlv-0001"
    good = sig.signature(secret, ts, dlv, body)
    assert sig.verify([secret], str(ts), good, dlv, body, now)
    assert sig.verify([secret, old], str(ts), sig.signature(old, ts, dlv, body), dlv, body, now)
    assert not sig.verify([secret], str(ts), good, dlv, body + b" ", now)  # tampered body
    assert not sig.verify(["whsec_other"], str(ts), good, dlv, body, now)  # wrong secret
    stale, future = ts - 301, ts + 301
    assert not sig.verify(
        [secret], str(stale), sig.signature(secret, stale, dlv, body), dlv, body, now
    )
    assert not sig.verify(
        [secret], str(future), sig.signature(secret, future, dlv, body), dlv, body, now
    )
    assert not sig.verify([secret], None, good, dlv, body, now)
    assert not sig.verify([secret], str(ts), None, dlv, body, now)
    assert not sig.verify([secret], "12abc", good, dlv, body, now)
    assert not sig.verify([], str(ts), good, dlv, body, now)  # revoked: no active secret


def test_the_delivery_id_is_signed_so_a_captured_request_cannot_be_rerun() -> None:
    """Review MAJOR 1: re-sending a captured request under a NEW delivery id fails."""
    secret, body, now = "whsec_current", b"{}", time.time()
    ts = int(now)
    captured = sig.signature(secret, ts, "dlv-original", body)
    assert sig.verify([secret], str(ts), captured, "dlv-original", body, now)
    assert not sig.verify([secret], str(ts), captured, "dlv-swapped", body, now)
    assert not sig.verify([secret], str(ts), captured, None, body, now)
    assert not sig.verify([secret], str(ts), captured, "x" * 129, body, now)


def test_outbound_header_carries_one_signature_per_active_secret() -> None:
    """Review MINOR 2: a rotated subscription keeps working for receivers with the old secret."""
    body, now = b'{"id":"e1"}', time.time()
    ts = int(now)
    header = sig.signature_header(["whsec_new", "whsec_old"], ts, "d1", body)
    assert header.count("v1=") == 2
    assert sig.verify(["whsec_old"], str(ts), header, "d1", body, now)
    assert sig.verify(["whsec_new"], str(ts), header, "d1", body, now)
    assert not sig.verify(["whsec_other"], str(ts), header, "d1", body, now)


# ------------------------------------------------------------------ targets
@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/webhook",  # public needs https
        "https://hooks.example.com:5678/webhook",  # public needs 443
        "https://127.0.0.1/webhook",  # private without allowlist
        "https://user:pw@hooks.example.com/webhook",  # credentials in URL
        "https://hooks.example.com/webhook#frag",
        "ftp://hooks.example.com/x",
        "https://2130706433/x",  # obfuscated loopback
    ],
)
def test_disallowed_webhook_targets(url: str) -> None:
    with pytest.raises(OmniGrowthError) as err:
        check_webhook_target(url, frozenset())
    assert err.value.code is ErrorCode.SSRF_BLOCKED


def test_allowlisted_self_hosted_n8n_targets() -> None:
    check_webhook_target("https://hooks.example.com/webhook/abc", frozenset())
    check_webhook_target("http://localhost:5678/webhook/abc", frozenset({"localhost:5678"}))
    check_webhook_target("http://n8n:5678/webhook/abc", frozenset({"n8n:5678"}))
    with pytest.raises(OmniGrowthError):
        check_webhook_target("http://localhost:9999/webhook/abc", frozenset({"localhost:5678"}))


def test_allowlist_entries_must_name_a_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review MINOR 5: host:port only (a bare host would open every port on it)."""
    from omnigrowth.services.integrations import target_allowlist

    monkeypatch.setenv("OMNIGROWTH_WEBHOOK_TARGET_ALLOWLIST", " LocalHost:5678 , [::1]:5678 ")
    get_settings.cache_clear()
    assert target_allowlist() == frozenset({"localhost:5678", "::1:5678"})
    check_webhook_target("http://[::1]:5678/webhook/x", target_allowlist())
    for bad in ("localhost", "http://n8n:5678", "n8n:5678/path", "user@n8n:5678"):
        monkeypatch.setenv("OMNIGROWTH_WEBHOOK_TARGET_ALLOWLIST", bad)
        get_settings.cache_clear()
        with pytest.raises(OmniGrowthError) as err:
            target_allowlist()
        assert err.value.code is ErrorCode.CONFIGURATION_ERROR, bad


async def test_safe_post_pins_the_checked_address_and_never_follows_redirects() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    async def public(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        return [ipaddress.ip_address("93.184.216.34")]

    status = await safe_post(
        "https://hooks.example.com/webhook/x",
        body=b"{}",
        headers={"X-OmniGrowth-Signature": "v1=00"},
        allowlist=frozenset(),
        timeout_s=5,
        resolver=public,
        transport=httpx.MockTransport(handler),
    )
    assert status == 302  # returned, not followed (counts as a failed attempt)
    assert len(seen) == 1
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "hooks.example.com"


async def test_safe_post_blocks_rebinding_and_metadata_even_when_allowlisted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not connect")

    async def private(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        return [ipaddress.ip_address("10.0.0.5")]

    async def metadata(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        return [ipaddress.ip_address("169.254.169.254")]

    transport = httpx.MockTransport(handler)
    with pytest.raises(OmniGrowthError) as err:
        await safe_post(
            "https://hooks.example.com/w",
            body=b"{}",
            headers={},
            allowlist=frozenset(),
            timeout_s=5,
            resolver=private,
            transport=transport,
        )
    assert err.value.code is ErrorCode.SSRF_BLOCKED
    with pytest.raises(OmniGrowthError):
        await safe_post(
            "http://n8n:5678/w",
            body=b"{}",
            headers={},
            allowlist=frozenset({"n8n:5678"}),
            timeout_s=5,
            resolver=metadata,
            transport=transport,
        )


# ------------------------------------------------------------------ backoff + payload
def test_backoff_grows_and_is_bounded() -> None:
    rng = random.Random(1)  # noqa: S311 - jitter test
    delays = [wd.backoff(n, rng).total_seconds() for n in range(1, 12)]
    assert delays[0] == pytest.approx(30, rel=0.11)
    assert all(b >= a * 0.8 for a, b in itertools.pairwise(delays))
    assert max(delays) <= 28800 * 1.1


def test_payload_is_minimal_deterministic_and_marks_simulation() -> None:
    event = IntegrationEvent(
        id=uuid.UUID(int=7),
        event_type="publication.failed",
        brand_id=uuid.UUID(int=1),
        subject_type="publication_jobs",
        subject_id=uuid.UUID(int=9),
        data={
            "publication_job_id": str(uuid.UUID(int=9)),
            "content_asset_id": str(uuid.UUID(int=3)),
            "publish_mode": "MOCK",
            "simulated": True,
        },
        occurred_at=datetime(2026, 9, 27, 12, 0, tzinfo=UTC),
    )
    body = wd.payload(event)
    assert body == wd.payload(event)  # identical on every attempt
    data = json.loads(body)
    assert set(data) == {"id", "type", "api_version", "occurred_at", "brand_id", "data", "links"}
    assert data["id"] == str(uuid.UUID(int=7)) and data["occurred_at"] == "2026-09-27T12:00:00Z"
    assert data["data"]["simulated"] is True
    assert data["links"]["content"].endswith(f"/content/{uuid.UUID(int=3)}")
    assert data["links"]["publishing"].endswith("/publications")


async def test_a_crashed_webhook_loop_is_logged_and_restarted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review MAJOR 6: an unexpected error must not silently stop notifications."""
    import asyncio
    import logging

    from omnigrowth.scheduling import __main__ as scheduler

    started: list[int] = []

    async def fake_loop(stop: asyncio.Event) -> None:
        started.append(1)
        await stop.wait()

    async def crash() -> None:
        raise ValueError("boom")

    monkeypatch.setattr(scheduler, "webhook_loop", fake_loop)
    stop = asyncio.Event()
    dead = asyncio.create_task(crash())
    await asyncio.sleep(0)
    with caplog.at_level(logging.ERROR):
        fresh = scheduler.supervise_webhooks(dead, stop)
    assert fresh is not dead
    await asyncio.sleep(0)
    assert started == [1]
    assert any("webhook loop stopped unexpectedly" in r.message for r in caplog.records)
    assert scheduler.supervise_webhooks(fresh, stop) is fresh  # alive: left alone
    stop.set()
    await fresh
