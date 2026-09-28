"""Phase 13: MastodonLivePublisher against recorded-shape responses (no network).

Response shapes follow the official docs (docs/PLATFORM_CAPABILITIES.md §4.9): Status
entities from POST /api/v1/statuses, StatusSource from /source, rate-limit headers. The
transport is in-memory; DNS is a fake resolver returning a public address.
"""

from __future__ import annotations

import ipaddress
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from omnigrowth.core.enums import ContentFormat, Platform
from omnigrowth.publishing.base import FailureKind, LookupStatus, PublishError, PublishRequest
from omnigrowth.publishing.mastodon import (
    MastodonAccountConfig,
    MastodonLivePublisher,
    status_length,
)

TOKEN = "mstdn_test_" + "Zk3" * 8  # test value only
BASE = "https://social.example"
PUBLIC_IP = ipaddress.ip_address("93.184.216.34")


async def _resolver(host: str, port: int) -> list[Any]:
    return [PUBLIC_IP]


def _status(sid: str, *, reply_to: str | None = None, created: str | None = None) -> dict[str, Any]:
    return {
        "id": sid,
        "uri": f"{BASE}/users/brand/statuses/{sid}",
        "url": f"{BASE}/@brand/{sid}",
        "created_at": created or "2026-09-27T12:00:00.000Z",
        "in_reply_to_id": reply_to,
        "visibility": "public",
        "favourites_count": 7,
        "reblogs_count": 2,
        "replies_count": 1,
    }


def _publisher(
    handler: Callable[[httpx.Request], httpx.Response], **config: Any
) -> MastodonLivePublisher:
    return MastodonLivePublisher(
        MastodonAccountConfig(instance_base_url=BASE, **config),
        SecretStr(TOKEN),
        allowlist=frozenset(),
        timeout_s=5,
        transport=httpx.MockTransport(handler),
        resolver=_resolver,
    )


def _request(*segments: str, fmt: ContentFormat = ContentFormat.TEXT_POST) -> PublishRequest:
    return PublishRequest(
        idempotency_key="a" * 64,
        platform=Platform.MASTODON,
        format=fmt,
        account_handle="@brand@social.example",
        segments=segments or ("Local AI keeps client data on your own machine.",),
        content_version_id=uuid.uuid4(),
    )


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.method == "POST"]


# ------------------------------------------------------------------ publish
async def test_a_text_post_is_sent_with_token_idempotency_key_and_visibility() -> None:
    rec = Recorder()

    def handler(request: httpx.Request) -> httpx.Response:
        rec.requests.append(request)
        return httpx.Response(200, json=_status("111"))

    result = await _publisher(handler, visibility="unlisted", language="en").publish(_request())
    assert (result.external_post_id, result.canonical_url) == ("111", f"{BASE}/@brand/111")
    assert result.published_at == datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    sent = rec.requests[0]
    assert sent.url.path == "/api/v1/statuses" and sent.url.host == str(PUBLIC_IP)  # pinned
    assert sent.headers["host"] == "social.example"
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert sent.headers["idempotency-key"] == "omnigrowth-" + "a" * 64 + "-0"
    assert rec.bodies() == [
        {
            "status": "Local AI keeps client data on your own machine.",
            "visibility": "unlisted",
            "language": "en",
        }
    ]


async def test_a_thread_is_posted_as_chained_replies_with_one_key_per_part() -> None:
    rec = Recorder()

    def handler(request: httpx.Request) -> httpx.Response:
        rec.requests.append(request)
        return httpx.Response(200, json=_status(str(100 + len(rec.requests))))

    result = await _publisher(handler).publish(
        _request("one", "two", "three", fmt=ContentFormat.THREAD)
    )
    assert result.external_post_id == "101"  # the thread is identified by its first post
    assert [b.get("in_reply_to_id") for b in rec.bodies()] == [None, "101", "102"]
    assert [r.headers["idempotency-key"][-2:] for r in rec.requests] == ["-0", "-1", "-2"]


@pytest.mark.parametrize(
    ("status", "headers", "kind"),
    [
        (401, {}, FailureKind.AUTH),
        (403, {}, FailureKind.AUTH),
        (422, {}, FailureKind.VALIDATION),
        (404, {}, FailureKind.VALIDATION),
        (409, {}, FailureKind.AFTER_SEND),  # undocumented 4xx: not proof nothing was created
        (408, {}, FailureKind.AFTER_SEND),
        (500, {}, FailureKind.AFTER_SEND),
        (503, {}, FailureKind.AFTER_SEND),
        (302, {"location": "https://elsewhere.example/"}, FailureKind.AFTER_SEND),
    ],
)
async def test_http_answers_map_to_outcome_classes(
    status: int, headers: dict[str, str], kind: FailureKind
) -> None:
    publisher = _publisher(lambda r: httpx.Response(status, headers=headers, json={"error": "x"}))
    with pytest.raises(PublishError) as info:
        await publisher.publish(_request())
    assert info.value.kind is kind
    assert TOKEN not in str(info.value)


async def test_rate_limit_is_retryable_after_the_reset_time() -> None:
    reset = (datetime.now(UTC) + timedelta(seconds=90)).isoformat().replace("+00:00", "Z")
    publisher = _publisher(
        lambda r: httpx.Response(
            429, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}
        )
    )
    with pytest.raises(PublishError) as info:
        await publisher.publish(_request())
    assert info.value.kind is FailureKind.RATE_LIMIT and info.value.retryable
    assert info.value.retry_after_s is not None and 80 <= info.value.retry_after_s <= 91


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (httpx.ConnectError("refused"), FailureKind.CONNECT_TIMEOUT),
        (httpx.ConnectTimeout("slow connect"), FailureKind.CONNECT_TIMEOUT),
        (httpx.ReadTimeout("no answer"), FailureKind.AFTER_SEND),
        (httpx.RemoteProtocolError("dropped"), FailureKind.AFTER_SEND),
    ],
)
async def test_transport_failures_distinguish_not_sent_from_maybe_sent(
    exc: Exception, kind: FailureKind
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(PublishError) as info:
        await _publisher(handler).publish(_request())
    assert info.value.kind is kind
    assert info.value.retryable is (kind is FailureKind.CONNECT_TIMEOUT)
    assert info.value.uncertain is (kind is FailureKind.AFTER_SEND)


async def test_an_unreadable_success_is_uncertain() -> None:
    publisher = _publisher(lambda r: httpx.Response(200, content=b"<html>proxy</html>"))
    with pytest.raises(PublishError) as info:
        await publisher.publish(_request())
    assert info.value.uncertain


@pytest.mark.parametrize("status", [422, 401, 429])
async def test_a_thread_cut_short_is_uncertain_whatever_the_second_answer(status: int) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_status("1")) if calls["n"] == 1 else httpx.Response(status)

    with pytest.raises(PublishError) as info:
        await _publisher(handler).publish(_request("one", "two", fmt=ContentFormat.THREAD))
    assert info.value.uncertain and "partially published (1 part" in str(info.value)


async def test_a_blocked_destination_is_a_proven_failure_before_sending() -> None:
    sent: list[httpx.Request] = []
    publisher = MastodonLivePublisher(
        MastodonAccountConfig(instance_base_url="http://127.0.0.1:3000"),
        SecretStr(TOKEN),
        allowlist=frozenset(),  # not allowlisted
        timeout_s=5,
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)),
    )
    with pytest.raises(PublishError) as info:
        await publisher.publish(_request())
    assert info.value.kind is FailureKind.VALIDATION and sent == []


def test_the_token_never_appears_in_repr() -> None:
    assert TOKEN not in repr(_publisher(lambda r: httpx.Response(200)))


# ------------------------------------------------------------------ validate / preview
def test_urls_count_as_the_reserved_length() -> None:
    text = "Read more: https://example.com/a/very/long/path/that/goes/on/and/on?x=1"
    assert status_length(text, 23) == len("Read more: ") + 23


@pytest.mark.parametrize(
    ("request_", "fragment"),
    [
        (_request("x" * 501), "501 characters"),
        (_request("a", "b"), "exactly one status"),
        (_request("a", fmt=ContentFormat.IMAGE_POST), "not supported"),
        (_request("   "), "empty post"),
    ],
)
def test_validation_refuses_before_any_call(request_: PublishRequest, fragment: str) -> None:
    publisher = _publisher(lambda r: pytest.fail("no call expected"))
    with pytest.raises(PublishError) as info:
        publisher.validate(request_)
    assert info.value.kind is FailureKind.VALIDATION and fragment in str(info.value)


def test_preview_shows_exactly_what_would_be_sent_and_no_secret() -> None:
    request = PublishRequest(
        idempotency_key="b" * 64,
        platform=Platform.MASTODON,
        format=ContentFormat.THREAD,
        account_handle="@brand@social.example",
        segments=("one", "two"),
        content_version_id=uuid.uuid4(),
        media_requirements=["screenshot of the dashboard"],
    )
    preview = _publisher(lambda r: httpx.Response(200), visibility="private").preview(request)
    assert preview["segments"] == ["one", "two"] and preview["visibility"] == "private"
    assert preview["media_attached"] == []
    assert preview["media_not_attached"] == ["screenshot of the dashboard"]
    assert preview["simulated"] is False and TOKEN not in json.dumps(preview)


# ------------------------------------------------------------------ lookup (reconcile)
def _lookup_server(
    statuses: list[dict[str, Any]], sources: dict[str, str], log: list[str]
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(f"{request.method} {request.url.path}")
        assert request.method == "GET", "a lookup never posts"
        path = request.url.path
        if path == "/api/v1/accounts/verify_credentials":
            return httpx.Response(200, json={"id": "42", "acct": "brand"})
        if path == "/api/v1/accounts/42/statuses":
            max_id = request.url.params.get("max_id")
            page = [s for s in statuses if max_id is None or int(s["id"]) < int(max_id)]
            return httpx.Response(200, json=page[:40])
        if path.endswith("/source"):
            sid = path.split("/")[-2]
            return httpx.Response(200, json={"id": sid, "text": sources[sid], "spoiler_text": ""})
        return httpx.Response(404)

    return handler


async def test_lookup_finds_a_post_by_exact_text_without_posting() -> None:
    log: list[str] = []
    statuses = [_status("9"), _status("8")]
    handler = _lookup_server(statuses, {"9": "something else", "8": "Hello world\n"}, log)
    outcome = await _publisher(handler).lookup(_request("Hello world"))
    assert outcome.status is LookupStatus.FOUND
    assert outcome.result is not None and outcome.result.external_post_id == "8"
    assert outcome.searched == 2 and not any(line.startswith("POST") for line in log)


async def test_lookup_requires_a_chained_thread_and_reports_partial() -> None:
    statuses = [_status("12", reply_to="11"), _status("11", reply_to="10"), _status("10")]
    texts = {"10": "one", "11": "two", "12": "three"}
    full = await _publisher(_lookup_server(statuses, texts, [])).lookup(
        _request("one", "two", "three", fmt=ContentFormat.THREAD)
    )
    assert full.status is LookupStatus.FOUND and full.found_post_ids == ("10", "11", "12")
    cut = await _publisher(_lookup_server(statuses[1:], texts, [])).lookup(
        _request("one", "two", "three", fmt=ContentFormat.THREAD)
    )
    assert cut.status is LookupStatus.PARTIAL and cut.result is None


async def test_lookup_not_found_is_bounded_and_honest() -> None:
    since = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    recent = [_status(str(i), created="2026-09-27T12:01:00Z") for i in range(200, 100, -1)]
    old = [_status("50", created="2026-09-20T12:00:00Z")]
    texts = {s["id"]: f"post {s['id']}" for s in recent + old}
    log: list[str] = []
    outcome = await _publisher(_lookup_server(recent + old, texts, log)).lookup(
        _request("never posted"), since=since
    )
    assert outcome.status is LookupStatus.NOT_FOUND and outcome.result is None
    assert outcome.searched <= 60 and "not among" in outcome.detail
    assert sum("/statuses?" in line or line.endswith("/statuses") for line in log) <= 3
    assert "GET /api/v1/statuses/50/source" not in log  # older than the dispatch window


# ------------------------------------------------------------------ metrics
async def test_metrics_map_counts_and_leave_unreported_fields_null() -> None:
    publisher = _publisher(lambda r: httpx.Response(200, json=_status("111")))
    metrics = await publisher.fetch_metrics(Platform.MASTODON, "111", seed="ignored")
    assert (metrics.values["likes"], metrics.values["shares"], metrics.values["comments"]) == (
        7,
        2,
        1,
    )
    assert metrics.values["impressions"] is None and metrics.values["clicks"] is None
    assert metrics.raw["favourites_count"] == 7


async def test_lookup_never_guesses_between_identical_posts_or_reuses_a_claimed_one() -> None:
    """Review MINOR 1: an identical post (manual, or another publication's) is not ours."""
    statuses = [_status("21"), _status("20")]
    texts = {"21": "Hello world", "20": "Hello world"}
    twice = await _publisher(_lookup_server(statuses, texts, [])).lookup(_request("Hello world"))
    assert twice.status is LookupStatus.AMBIGUOUS and twice.result is None
    claimed = await _publisher(_lookup_server(statuses, texts, [])).lookup(
        _request("Hello world"), exclude_ids=frozenset({"20"})
    )
    assert claimed.status is LookupStatus.FOUND
    assert claimed.result is not None and claimed.result.external_post_id == "21"
