"""Health schemas (API_PLAN §2.1).

``HealthRead`` is the public, unauthenticated contract: component states only — no
hostnames, versions, model names or error detail. ``HealthReport`` is the internal
detailed result (latency + masked detail) used for logging and, from Phase 4, an
OWNER-only details endpoint.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from omnigrowth.core.enums import HealthStatus


class CheckResult(BaseModel):
    status: HealthStatus
    latency_ms: float | None = None
    detail: str | None = None


class HealthChecks(BaseModel):
    database: CheckResult
    redis: CheckResult
    local_llm: CheckResult


class HealthReport(BaseModel):
    """Internal detailed report. Never returned by the unauthenticated endpoint."""

    status: HealthStatus
    checks: HealthChecks


class ComponentStatuses(BaseModel):
    database: HealthStatus
    redis: HealthStatus
    local_llm: HealthStatus


class HealthRead(BaseModel):
    """Public health response (API_PLAN §2.1)."""

    status: HealthStatus
    time: datetime
    checks: ComponentStatuses

    @classmethod
    def from_report(cls, report: HealthReport, now: datetime) -> HealthRead:
        return cls(
            status=report.status,
            time=now,
            checks=ComponentStatuses(
                database=report.checks.database.status,
                redis=report.checks.redis.status,
                local_llm=report.checks.local_llm.status,
            ),
        )
