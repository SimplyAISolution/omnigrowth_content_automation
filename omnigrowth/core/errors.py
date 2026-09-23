"""Categorized error taxonomy (spec §29, GLOSSARY ErrorCode).

Every failure that crosses a module boundary is raised as ``OmniGrowthError`` with an
``ErrorCode``. Bare ``except:`` / swallowed exceptions are forbidden (AGENTS.md §3).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    LLM_OUTPUT_INVALID = "LLM_OUTPUT_INVALID"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    PRIVACY_VIOLATION = "PRIVACY_VIOLATION"
    PLATFORM_AUTH_FAILURE = "PLATFORM_AUTH_FAILURE"
    PLATFORM_RATE_LIMIT = "PLATFORM_RATE_LIMIT"
    PLATFORM_TIMEOUT = "PLATFORM_TIMEOUT"
    PUBLICATION_UNCERTAIN = "PUBLICATION_UNCERTAIN"
    LIVE_PUBLISH_BLOCKED = "LIVE_PUBLISH_BLOCKED"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    INGESTION_FAILURE = "INGESTION_FAILURE"
    RESEARCH_FAILURE = "RESEARCH_FAILURE"
    DATABASE_FAILURE = "DATABASE_FAILURE"
    QUEUE_FAILURE = "QUEUE_FAILURE"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# HTTP status per code (API_PLAN §1 error mapping). Used by the API exception handler.
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.MODEL_TIMEOUT: 504,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.PROVIDER_RATE_LIMIT: 503,
    ErrorCode.LLM_OUTPUT_INVALID: 502,
    ErrorCode.BUDGET_EXCEEDED: 402,
    ErrorCode.PRIVACY_VIOLATION: 403,
    ErrorCode.PLATFORM_AUTH_FAILURE: 502,
    ErrorCode.PLATFORM_RATE_LIMIT: 503,
    ErrorCode.PLATFORM_TIMEOUT: 504,
    ErrorCode.PUBLICATION_UNCERTAIN: 409,
    ErrorCode.LIVE_PUBLISH_BLOCKED: 403,
    ErrorCode.VALIDATION_FAILURE: 422,
    ErrorCode.INVALID_STATE_TRANSITION: 409,
    ErrorCode.SSRF_BLOCKED: 422,
    ErrorCode.INGESTION_FAILURE: 422,
    ErrorCode.RESEARCH_FAILURE: 502,
    ErrorCode.DATABASE_FAILURE: 503,
    ErrorCode.QUEUE_FAILURE: 503,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.CONFIGURATION_ERROR: 500,
    ErrorCode.INTERNAL_ERROR: 500,
}


class OmniGrowthError(Exception):
    """Base error. ``message`` is safe to show users; ``details`` must already be masked."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.code]


class InvalidStateTransitionError(OmniGrowthError):
    def __init__(self, entity: str, from_state: str, to_state: str, reason: str) -> None:
        super().__init__(
            ErrorCode.INVALID_STATE_TRANSITION,
            f"{entity}: {from_state} -> {to_state} not allowed ({reason})",
            details={"entity": entity, "from": from_state, "to": to_state, "reason": reason},
        )


class ConfigurationError(OmniGrowthError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(ErrorCode.CONFIGURATION_ERROR, message, details=details)
