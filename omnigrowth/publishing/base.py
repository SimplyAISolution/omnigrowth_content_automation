"""Platform publisher interface (spec §15) and publish outcomes (STATE_MACHINES §3.5).

Business logic never talks to a platform directly: the scheduler hands a ``PublishRequest``
to a ``PlatformPublisher`` and maps the typed outcome onto the publication-job machine.
Only two failure kinds are *proven not published* and retryable (rate limit, pre-send
timeout); anything that might have reached the platform is ``UNCERTAIN`` and is never
retried automatically (§14, A3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from omnigrowth.core.enums import ContentFormat, Platform, PublishMode
from omnigrowth.core.errors import ErrorCode


@dataclass(frozen=True, slots=True)
class PublishRequest:
    idempotency_key: str
    platform: Platform
    format: ContentFormat
    account_handle: str
    segments: tuple[str, ...]  # composed text; one per post for threads
    content_version_id: uuid.UUID
    media_requirements: list[Any] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PublishResult:
    external_post_id: str
    canonical_url: str
    published_at: datetime


class FailureKind(StrEnum):
    RATE_LIMIT = "RATE_LIMIT"  # proven not published, retryable
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"  # before the request was sent: retryable
    AFTER_SEND = "AFTER_SEND"  # timeout / 5xx / garbage after send: UNCERTAIN
    AUTH = "AUTH"  # 401/403: proven not published, not retried
    VALIDATION = "VALIDATION"  # 4xx content rejection: not retried


_CODES = {
    FailureKind.RATE_LIMIT: ErrorCode.PLATFORM_RATE_LIMIT,
    FailureKind.CONNECT_TIMEOUT: ErrorCode.PLATFORM_TIMEOUT,
    FailureKind.AFTER_SEND: ErrorCode.PUBLICATION_UNCERTAIN,
    FailureKind.AUTH: ErrorCode.PLATFORM_AUTH_FAILURE,
    FailureKind.VALIDATION: ErrorCode.VALIDATION_FAILURE,
}


class PublishError(Exception):
    """Typed publish failure. Not an ``OmniGrowthError``: the dispatcher maps it."""

    def __init__(
        self, kind: FailureKind, message: str, *, retry_after_s: float | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_after_s = retry_after_s

    @property
    def code(self) -> ErrorCode:
        return _CODES[self.kind]

    @property
    def retryable(self) -> bool:
        return self.kind in (FailureKind.RATE_LIMIT, FailureKind.CONNECT_TIMEOUT)

    @property
    def uncertain(self) -> bool:
        return self.kind is FailureKind.AFTER_SEND


@dataclass(frozen=True, slots=True)
class MetricsResult:
    """Raw platform metrics. ``None`` = not provided by the platform (never 0)."""

    values: dict[str, int | None]
    raw: dict[str, Any] = field(default_factory=dict)


class PlatformPublisher(Protocol):
    mode: PublishMode
    # True when publish() changes the outside world (drives J10 vs J11 on lease expiry).
    external_side_effect: bool

    def validate(self, request: PublishRequest) -> None: ...

    async def publish(self, request: PublishRequest) -> PublishResult: ...

    async def lookup(self, request: PublishRequest) -> PublishResult | None: ...

    async def fetch_metrics(
        self, platform: Platform, external_post_id: str, *, seed: str
    ) -> MetricsResult: ...
