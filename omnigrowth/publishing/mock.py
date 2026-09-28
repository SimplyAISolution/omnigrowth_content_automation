"""``MockPublisher`` (spec §34, STATE_MACHINES §3.2): validates content, simulates
publication, returns synthetic ids, zero network I/O.

* The same idempotency key always yields the same ``external_post_id`` (A3).
* ``faults`` injects failures per call number, for tests of the §3.5 error classes.
* Metrics are synthetic but deterministic from ``seed`` (content hash + platform), so
  analytics and the Optimization Agent's prompt are reproducible (A8 fixtures).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from omnigrowth.core.enums import Platform, PublishMode
from omnigrowth.publishing.base import (
    FailureKind,
    LookupOutcome,
    LookupStatus,
    MetricsResult,
    PublishError,
    PublishRequest,
    PublishResult,
)

# Which metrics each platform reports in the mock (others stay None, like real APIs).
_REPORTED: dict[Platform, tuple[str, ...]] = {
    Platform.LINKEDIN: (
        "impressions",
        "likes",
        "comments",
        "shares",
        "clicks",
        "profile_visits",
        "followers_gained",
    ),
    Platform.X: ("impressions", "likes", "comments", "shares", "saves", "clicks"),
}
_DEFAULT_REPORTED = ("impressions", "likes", "comments", "shares")


def mock_post_id(idempotency_key: str) -> str:
    return "mock_" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]


class MockPublisher:
    mode = PublishMode.MOCK
    external_side_effect = True  # treated like a real post (J11 on lease expiry)

    def __init__(
        self,
        faults: Iterable[FailureKind | None] = (),
        *,
        fault_versions: Iterable[str] | None = None,
    ) -> None:
        self._faults = list(faults)
        # When given, injected faults only apply to these content version ids.
        self._fault_versions = set(fault_versions) if fault_versions is not None else None
        self.sent: dict[str, PublishResult] = {}
        self.calls = 0
        self.requests: list[PublishRequest] = []  # every publish() call, for inspection

    def validate(self, request: PublishRequest) -> None:
        if not request.segments or not all(s.strip() for s in request.segments):
            raise PublishError(FailureKind.VALIDATION, "empty post")

    async def publish(self, request: PublishRequest) -> PublishResult:
        self.validate(request)
        self.calls += 1
        self.requests.append(request)
        targeted = (
            self._fault_versions is None or str(request.content_version_id) in self._fault_versions
        )
        fault = self._faults.pop(0) if self._faults and targeted else None
        if fault in (FailureKind.RATE_LIMIT, FailureKind.CONNECT_TIMEOUT, FailureKind.AUTH):
            raise PublishError(fault, f"mock {fault.value.lower()}", retry_after_s=None)
        post_id = mock_post_id(request.idempotency_key)
        result = self.sent.get(request.idempotency_key) or PublishResult(
            external_post_id=post_id,
            canonical_url=f"mock://{request.platform.value.lower()}/{post_id}",
            published_at=datetime.now(UTC),
        )
        # The post "exists" even when the response is lost (AFTER_SEND): that is exactly
        # the case where a retry would duplicate it.
        self.sent[request.idempotency_key] = result
        if fault is FailureKind.AFTER_SEND:
            raise PublishError(FailureKind.AFTER_SEND, "mock timeout after send")
        return result

    async def lookup(
        self,
        request: PublishRequest,
        *,
        since: datetime | None = None,
        exclude_ids: frozenset[str] = frozenset(),
    ) -> LookupOutcome:
        found = self.sent.get(request.idempotency_key)
        if found is None:
            return LookupOutcome(LookupStatus.NOT_FOUND, detail="not in this mock's memory")
        return LookupOutcome(
            LookupStatus.FOUND, result=found, found_post_ids=(found.external_post_id,)
        )

    def preview(self, request: PublishRequest) -> dict[str, Any]:
        return {
            "publisher": "MockPublisher",
            "simulated": True,
            "account": request.account_handle,
            "segments": list(request.segments),
            "media_attached": [],
            "media_not_attached": list(request.media_requirements),
        }

    async def fetch_metrics(
        self, platform: Platform, external_post_id: str, *, seed: str
    ) -> MetricsResult:
        digest = hashlib.sha256(f"{platform.value}:{seed}".encode()).digest()
        impressions = 800 + int.from_bytes(digest[0:2], "big") % 4200

        def part(index: int, per_mille: int) -> int:
            return impressions * (1 + digest[index] % per_mille) // 1000

        values: dict[str, int | None] = {
            "impressions": impressions,
            "likes": part(2, 60),
            "comments": part(3, 12),
            "shares": part(4, 10),
            "saves": part(5, 8),
            "clicks": part(6, 25),
            "profile_visits": part(7, 15),
            "followers_gained": digest[8] % 12,
        }
        reported = _REPORTED.get(platform, _DEFAULT_REPORTED)
        values = {k: (v if k in reported else None) for k, v in values.items()}
        raw = {"mock": True, "external_post_id": external_post_id}
        return MetricsResult(values=values, raw=raw)
