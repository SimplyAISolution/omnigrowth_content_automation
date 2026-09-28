"""Durable async jobs (DATA_MODEL §6a.1; ADR-0003): a ``jobs`` row is the source of truth,
RQ only carries the job id. Status changes go through the JOB state machine."""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from sqlalchemy import Table, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import ActorType, JobKind, JobStatus
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.logging import get_context
from omnigrowth.core.masking import mask_mapping
from omnigrowth.core.settings import get_settings
from omnigrowth.core.state_machines import JOB, Actor, apply_transition, create_with_state
from omnigrowth.models import AuditEvent, Job

log = logging.getLogger(__name__)

_JOBS = cast(Table, Job.__table__)
_AUDIT = cast(Table, AuditEvent.__table__)

# RQ queue per job kind (compose worker listens on all of them).
QUEUE_FOR_KIND: dict[JobKind, str] = {
    JobKind.SOURCE_INGEST: "ingest",
    JobKind.EMBEDDING: "embed",
    JobKind.REEMBED: "embed",
    JobKind.CAMPAIGN_PLANNING: "agents",
    JobKind.ASSET_GENERATION: "generate",
    JobKind.CONTENT_REGENERATION: "generate",
    JobKind.QA_RUN: "qa",
    JobKind.ANALYTICS_PULL: "analytics",
    JobKind.EXPERIMENT_EVALUATION: "analytics",  # pure DB math, no model
}
JOB_ENTRYPOINT = "omnigrowth.workflows.tasks.run_job"

# RQ time budget per queue. Model-bound queues run several local LLM calls in sequence
# (Phase 5 live planning ~4 min; Phase 6 live generation of 7 assets ~8.5 min on a 9B
# model), so they get far more than parsing/ingest jobs.
DEFAULT_JOB_TIMEOUT_S = 600
JOB_TIMEOUT_S: dict[str, int] = {"agents": 1800, "generate": 3600, "qa": 900}


# Queues whose jobs call the local model; their backlog is bounded for human requests.
MODEL_QUEUES = frozenset({"agents", "generate", "qa", "embed"})


def job_timeout_s(queue_name: str) -> int:
    return JOB_TIMEOUT_S.get(queue_name, DEFAULT_JOB_TIMEOUT_S)


def queue_for(kind: JobKind) -> str:
    return QUEUE_FOR_KIND.get(kind, "agents")


class JobQueue(Protocol):
    def enqueue(self, queue_name: str, job_id: uuid.UUID) -> None: ...


class RqJobQueue:
    def __init__(self, redis_url: str) -> None:
        self._redis = Redis.from_url(redis_url)

    def enqueue(self, queue_name: str, job_id: uuid.UUID) -> None:
        try:
            Queue(queue_name, connection=self._redis).enqueue(
                JOB_ENTRYPOINT,
                str(job_id),
                job_id=str(job_id),
                job_timeout=job_timeout_s(queue_name),
            )
        except RedisError as exc:
            raise OmniGrowthError(
                ErrorCode.QUEUE_FAILURE, "could not enqueue job", details={"job_id": str(job_id)}
            ) from exc


@dataclass
class RecordingJobQueue:
    """Test double: records enqueues; tests execute ``run_job`` themselves."""

    enqueued: list[tuple[str, uuid.UUID]] = field(default_factory=list)

    def enqueue(self, queue_name: str, job_id: uuid.UUID) -> None:
        self.enqueued.append((queue_name, job_id))


@dataclass(frozen=True, slots=True)
class JobView:
    id: uuid.UUID
    kind: JobKind
    status: JobStatus
    subject_type: str | None
    subject_id: uuid.UUID | None
    brand_id: uuid.UUID | None
    result: dict[str, Any] | None
    error_code: ErrorCode | None
    error: dict[str, Any] | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _view(job: Job) -> JobView:
    return JobView(
        id=job.id,
        kind=JobKind(job.kind),
        status=JobStatus(job.status),
        subject_type=job.subject_type,
        subject_id=job.subject_id,
        brand_id=job.brand_id,
        result=job.result,
        error_code=ErrorCode(job.error_code) if job.error_code else None,
        error=job.error,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _correlation() -> str:
    return get_context().get("correlation_id") or uuid7().hex


async def create_job(
    session: AsyncSession,
    *,
    kind: JobKind,
    actor: Actor,
    brand_id: uuid.UUID | None,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    max_open_jobs: int | None = None,
) -> uuid.UUID:
    """Insert a QUEUED job row (caller commits, then calls ``dispatch``).

    Human requests are refused (429 RATE_LIMITED) once the target model queue already holds
    ``max_open_model_jobs`` open jobs. System/scheduler follow-ups (e.g. a QA re-queue) are
    never refused, so no work is stranded half-way.
    """
    # Requests from people and from integrations share the bounded model-job backlog.
    if actor.type in (ActorType.HUMAN, ActorType.INTEGRATION):
        await _check_backlog(session, kind, max_open_jobs)
    job_id = uuid7()
    await create_with_state(
        session,
        machine=JOB,
        table=_JOBS,
        audit_table=_AUDIT,
        row={
            "id": job_id,
            "brand_id": brand_id,
            "kind": kind,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "requested_by_type": actor.type,
            "requested_by_id": actor.id,
            "correlation_id": _correlation(),
        },
        trigger="enqueue",
        actor=actor,
        correlation_id=_correlation(),
        brand_id=brand_id,
    )
    return job_id


def _queue_lock_id(queue_name: str) -> int:
    """Stable signed 64-bit advisory-lock id for one queue's backlog check."""
    digest = hashlib.sha256(f"omnigrowth:backlog:{queue_name}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


async def _check_backlog(session: AsyncSession, kind: JobKind, limit: int | None) -> None:
    queue_name = queue_for(kind)
    if queue_name not in MODEL_QUEUES:
        return
    if limit is None:
        limit = get_settings().max_open_model_jobs
    # Serialise count-then-insert per queue: the transaction-scoped advisory lock is held
    # until this request commits its job row, so concurrent requests see each other's rows
    # and the cap cannot be overshot (reviewer MINOR m8).
    await session.execute(select(func.pg_advisory_xact_lock(_queue_lock_id(queue_name))))
    kinds = [k for k in JobKind if queue_for(k) == queue_name]
    open_jobs = await session.scalar(
        select(func.count())
        .select_from(_JOBS)
        .where(
            _JOBS.c.kind.in_(kinds),
            _JOBS.c.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
    )
    if (open_jobs or 0) >= limit:
        raise OmniGrowthError(
            ErrorCode.RATE_LIMITED,
            f"the {queue_name} queue is full; retry when current jobs finish",
            details={"limit": "max_open_model_jobs", "max": limit, "queue": queue_name},
        )


def dispatch(queue: JobQueue, kind: JobKind, job_id: uuid.UUID) -> None:
    queue.enqueue(queue_for(kind), job_id)


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> JobView:
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "job not found")
    return _view(job)


def system_actor(name: str) -> Actor:
    return Actor(ActorType.SYSTEM, name)


async def mark_running(session: AsyncSession, job: JobView) -> None:
    await apply_transition(
        session,
        machine=JOB,
        table=_JOBS,
        audit_table=_AUDIT,
        entity_id=job.id,
        from_state=JobStatus.QUEUED,
        trigger="start",
        actor=system_actor("worker"),
        correlation_id=_correlation(),
        brand_id=job.brand_id,
        values={"started_at": datetime.now(UTC), "attempt_count": 1},
    )


async def mark_succeeded(session: AsyncSession, job: JobView, result: Mapping[str, Any]) -> None:
    await apply_transition(
        session,
        machine=JOB,
        table=_JOBS,
        audit_table=_AUDIT,
        entity_id=job.id,
        from_state=JobStatus.RUNNING,
        trigger="succeed",
        actor=system_actor("worker"),
        correlation_id=_correlation(),
        brand_id=job.brand_id,
        values={"finished_at": datetime.now(UTC), "result": dict(result)},
    )


# Details that must not be shown to API readers of a job (internal network layout, spec 26).
_PRIVATE_DETAIL_KEYS = frozenset({"address", "host", "resolved_ip"})


def public_error(exc: OmniGrowthError) -> dict[str, Any]:
    details = {k: v for k, v in exc.details.items() if k not in _PRIVATE_DETAIL_KEYS}
    return mask_mapping({"message": exc.message, **details})


async def cancel_queued(session: AsyncSession, job_id: uuid.UUID, reason: str) -> None:
    """QUEUED -> CANCELLED (e.g. the queue was unreachable, so no worker will ever run it)."""
    job = await get_job(session, job_id)
    await apply_transition(
        session,
        machine=JOB,
        table=_JOBS,
        audit_table=_AUDIT,
        entity_id=job_id,
        from_state=JobStatus.QUEUED,
        trigger="cancel",
        actor=system_actor("api"),
        correlation_id=_correlation(),
        brand_id=job.brand_id,
        values={"finished_at": datetime.now(UTC), "error_code": ErrorCode.QUEUE_FAILURE},
        reason=reason,
    )


async def stuck_running_jobs(session: AsyncSession, older_than: datetime) -> list[JobView]:
    rows = (
        await session.execute(
            select(Job).where(Job.status == JobStatus.RUNNING, Job.started_at < older_than)
        )
    ).scalars()
    return [_view(r) for r in rows]


async def reap(session: AsyncSession, job: JobView) -> None:
    await apply_transition(
        session,
        machine=JOB,
        table=_JOBS,
        audit_table=_AUDIT,
        entity_id=job.id,
        from_state=JobStatus.RUNNING,
        trigger="reap",
        actor=Actor(ActorType.SCHEDULER, "reaper"),
        correlation_id=_correlation(),
        brand_id=job.brand_id,
        values={
            "finished_at": datetime.now(UTC),
            "error_code": ErrorCode.QUEUE_FAILURE,
            "error": {
                "message": "job exceeded its time budget or the worker died",
                "reason": "STUCK",
            },
        },
        reason="STUCK",
    )


async def mark_failed(session: AsyncSession, job: JobView, exc: OmniGrowthError) -> None:
    await apply_transition(
        session,
        machine=JOB,
        table=_JOBS,
        audit_table=_AUDIT,
        entity_id=job.id,
        from_state=JobStatus.RUNNING,
        trigger="fail",
        actor=system_actor("worker"),
        correlation_id=_correlation(),
        brand_id=job.brand_id,
        values={
            "finished_at": datetime.now(UTC),
            "error_code": exc.code,
            "error": public_error(exc),
        },
        reason=str(exc.code),
    )
