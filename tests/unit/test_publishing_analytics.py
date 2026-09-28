"""Unit tests: Phase 7 publisher, backoff, idempotency key, derived metrics, canned matching."""

from __future__ import annotations

import json
import random
import uuid
from pathlib import Path

import pytest

from omnigrowth.analytics.metrics import DEFINITIONS, derive, derive_all, sum_counts
from omnigrowth.core.enums import ContentFormat, Platform
from omnigrowth.providers.base import ChatRequest
from omnigrowth.providers.fake import CannedProvider
from omnigrowth.publishing.base import FailureKind, PublishError, PublishRequest
from omnigrowth.publishing.config import backoff_seconds, load_publishing_config
from omnigrowth.publishing.mock import MockPublisher, mock_post_id
from omnigrowth.services.publishing import idempotency_key

REPO = Path(__file__).resolve().parents[2]
CONFIG = load_publishing_config(REPO / "config")


def _request(key: str = "k" * 64, version: uuid.UUID | None = None) -> PublishRequest:
    return PublishRequest(
        idempotency_key=key,
        platform=Platform.LINKEDIN,
        format=ContentFormat.TEXT_POST,
        account_handle="brand",
        segments=("Hello world",),
        content_version_id=version or uuid.uuid4(),
    )


# ------------------------------------------------------------------ idempotency (A3)
def test_idempotency_key_is_sha256_of_lowercase_version_and_account() -> None:
    version = uuid.UUID("0190F1C2-0000-7000-8000-000000000001")
    account = uuid.UUID("0190F1C2-0000-7000-8000-000000000002")
    import hashlib

    expected = hashlib.sha256(f"{str(version).lower()}:{str(account).lower()}".encode()).hexdigest()
    assert idempotency_key(version, account) == expected
    assert idempotency_key(version, account) != idempotency_key(account, version)


# ------------------------------------------------------------------ mock publisher (§34)
async def test_mock_publisher_is_idempotent_per_key() -> None:
    publisher = MockPublisher()
    first = await publisher.publish(_request("a" * 64))
    again = await publisher.publish(_request("a" * 64))
    other = await publisher.publish(_request("b" * 64))
    assert first.external_post_id == again.external_post_id == mock_post_id("a" * 64)
    assert other.external_post_id != first.external_post_id
    assert first.canonical_url.startswith("mock://linkedin/")


async def test_after_send_fault_leaves_a_post_behind_for_reconciliation() -> None:
    publisher = MockPublisher(faults=[FailureKind.AFTER_SEND])
    request = _request("c" * 64)
    with pytest.raises(PublishError) as exc:
        await publisher.publish(request)
    assert exc.value.uncertain and not exc.value.retryable
    assert exc.value.code.value == "PUBLICATION_UNCERTAIN"
    assert (await publisher.lookup(request)).status == "FOUND"  # it did get posted


@pytest.mark.parametrize(
    ("kind", "retryable", "code"),
    [
        (FailureKind.RATE_LIMIT, True, "PLATFORM_RATE_LIMIT"),
        (FailureKind.CONNECT_TIMEOUT, True, "PLATFORM_TIMEOUT"),
        (FailureKind.AUTH, False, "PLATFORM_AUTH_FAILURE"),
    ],
)
async def test_proven_not_published_faults(kind: FailureKind, retryable: bool, code: str) -> None:
    publisher = MockPublisher(faults=[kind])
    request = _request("d" * 64)
    with pytest.raises(PublishError) as exc:
        await publisher.publish(request)
    assert exc.value.retryable is retryable and exc.value.code.value == code
    assert (await publisher.lookup(request)).status == "NOT_FOUND"  # nothing was posted


async def test_faults_can_be_scoped_to_versions() -> None:
    mine = uuid.uuid4()
    publisher = MockPublisher(faults=[FailureKind.AUTH], fault_versions={str(mine)})
    await publisher.publish(_request("e" * 64, version=uuid.uuid4()))  # not targeted
    with pytest.raises(PublishError):
        await publisher.publish(_request("f" * 64, version=mine))


async def test_empty_content_is_rejected_by_validation() -> None:
    request = PublishRequest(
        idempotency_key="g" * 64,
        platform=Platform.X,
        format=ContentFormat.TEXT_POST,
        account_handle="brand",
        segments=("  ",),
        content_version_id=uuid.uuid4(),
    )
    with pytest.raises(PublishError) as exc:
        await MockPublisher().publish(request)
    assert exc.value.kind is FailureKind.VALIDATION


async def test_mock_metrics_are_deterministic_and_platform_shaped() -> None:
    publisher = MockPublisher()
    a = await publisher.fetch_metrics(Platform.X, "mock_1", seed="hash:X")
    b = await publisher.fetch_metrics(Platform.X, "mock_2", seed="hash:X")
    assert a.values == b.values  # seed, not the random post id, drives the numbers
    assert a.values["profile_visits"] is None  # X reports no profile visits in the mock
    li = await publisher.fetch_metrics(Platform.LINKEDIN, "mock_3", seed="hash:LI")
    assert li.values["saves"] is None and li.values["profile_visits"] is not None


# ------------------------------------------------------------------ backoff (§3.5)
def test_backoff_is_bounded_full_jitter_and_honours_retry_after() -> None:
    rng = random.Random(7)  # noqa: S311 - deterministic jitter for the test, not crypto
    cfg = CONFIG.publishing
    for attempt in range(1, 12):
        delay = backoff_seconds(attempt, cfg, rng=rng)
        assert (
            0
            <= delay
            <= min(cfg.backoff_max_seconds, cfg.backoff_base_seconds * 2 ** (attempt - 1))
        )
    assert backoff_seconds(1, cfg, retry_after_s=120, rng=rng) >= 120
    assert backoff_seconds(1, cfg, retry_after_s=10**9, rng=rng) == cfg.backoff_max_seconds


def test_publishing_config_requires_lease_longer_than_request(tmp_path: Path) -> None:
    (tmp_path / "publishing.yaml").write_text(
        "scheduler: {tick_seconds: 5, batch_size: 5, lease_seconds: 30,"
        " request_timeout_seconds: 30}\n"
        "publishing: {max_attempts: 3, backoff_base_seconds: 1, backoff_max_seconds: 10,"
        " uncertain_hold_seconds: 60, schedule_grace_seconds: 60}\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match=r"publishing\.yaml"):
        load_publishing_config(tmp_path)


# ------------------------------------------------------------------ derived metrics (§17)
def test_rates_are_null_safe_with_reasons() -> None:
    engagement = next(d for d in DEFINITIONS if d.key == "engagement_rate")
    assert derive({"impressions": None, "likes": 5}, engagement).null_reason == (
        "impressions not captured"
    )
    assert derive({"impressions": 0, "likes": 5}, engagement).null_reason == "impressions is 0"
    none = derive({"impressions": 100}, engagement)
    assert none.value is None and set(none.missing_inputs) == {
        "likes",
        "comments",
        "shares",
        "saves",
    }
    partial = derive({"impressions": 200, "likes": 10, "comments": 10}, engagement)
    assert partial.value == pytest.approx(0.1)
    assert set(partial.missing_inputs) == {"shares", "saves"}


def test_follow_conversion_and_totals_never_turn_null_into_zero() -> None:
    derived = derive_all({"followers_gained": 3, "profile_visits": 30, "impressions": 1000})
    assert derived["follow_conversion"].value == pytest.approx(0.1)
    totals = sum_counts([{"likes": 3, "saves": None}, {"likes": 4, "saves": None}])
    assert totals["likes"] == 7 and totals["saves"] is None and totals["impressions"] is None


# ------------------------------------------------------------------ canned matching (A8)
async def test_canned_provider_serves_matching_entries_first(tmp_path: Path) -> None:
    (tmp_path / "campaign__planner.json").write_text(
        json.dumps({"responses": [{"plain": 1}, {"match": "YOUTUBE", "response": {"yt": 1}}]}),
        encoding="utf-8",
    )
    canned = CannedProvider(tmp_path)

    def req(text: str) -> ChatRequest:
        return ChatRequest(
            model="m",
            messages=[{"role": "user", "content": text}],
            meta={"prompt_id": "campaign/planner"},
        )

    assert json.loads((await canned.chat(req("LINKEDIN; YOUTUBE"))).text) == {"yt": 1}
    assert json.loads((await canned.chat(req("LINKEDIN; X"))).text) == {"plain": 1}


def test_aggregate_rates_only_use_inputs_every_row_captured() -> None:
    from omnigrowth.analytics.metrics import derive_aggregate

    engagement = next(d for d in DEFINITIONS if d.key == "engagement_rate")
    linkedin = {"impressions": 1000, "likes": 50, "saves": None}
    x = {"impressions": 1000, "likes": 30, "saves": 20}
    value = derive_aggregate([linkedin, x], engagement)
    # saves were not captured for LinkedIn, so they are excluded for everyone (not 0).
    assert value.value == pytest.approx(80 / 2000)
    assert "saves" in value.missing_inputs
    no_denominator = derive_aggregate([{"likes": 3}], engagement)
    assert no_denominator.value is None and no_denominator.null_reason == "impressions not captured"
