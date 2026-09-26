"""RQ job entrypoints. RQ passes only the job id; everything else is reloaded from Postgres
(jobs are idempotent and safe to re-run, ARCHITECTURE §5).

Each RQ job runs in its own event loop (``aio.run``), so the DB engine is disposed at the end
of every job — pooled connections are bound to the loop that created them.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from rq.timeouts import JobTimeoutException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.agents.base import AgentDeps
from omnigrowth.campaigns.orchestrator import plan_campaign
from omnigrowth.content.qa import cached_qa_config
from omnigrowth.core import aio
from omnigrowth.core.enums import JobKind, JobStatus
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.logging import bind_context
from omnigrowth.core.settings import get_settings
from omnigrowth.prompts import PromptRegistry
from omnigrowth.providers.factory import build_router
from omnigrowth.services import analytics as analytics_service
from omnigrowth.services import campaigns as campaign_service
from omnigrowth.services import content as content_service
from omnigrowth.services import jobs as job_service
from omnigrowth.services import recommendations as recommendation_service
from omnigrowth.services import sources as source_service
from omnigrowth.storage.campaigns import fail_running_agent_runs
from omnigrowth.storage.db import get_engine, get_sessionmaker
from omnigrowth.storage.uploads import UploadStore

log = logging.getLogger(__name__)

# Non-domain exceptions a job may raise (DB, network, decoding, RQ time budget). Anything
# else is a programming error: it propagates and the reaper fails the job later.
_UNEXPECTED: tuple[type[BaseException], ...] = (
    SQLAlchemyError,
    httpx.HTTPError,
    OSError,
    UnicodeError,
    ValueError,
    JobTimeoutException,
)


def _crash_code(kind: JobKind, exc: BaseException) -> ErrorCode:
    if isinstance(exc, SQLAlchemyError):
        return ErrorCode.DATABASE_FAILURE
    if kind is JobKind.SOURCE_INGEST:
        return ErrorCode.INGESTION_FAILURE
    if isinstance(exc, JobTimeoutException):
        # A planning job only runs this long when the model is slow.
        return ErrorCode.MODEL_TIMEOUT
    return ErrorCode.INTERNAL_ERROR


Handler = Callable[[AsyncSession, job_service.JobView], Awaitable[dict[str, Any]]]


def _handlers(
    store: UploadStore | None,
    fetcher: source_service.Fetcher | None,
    agent_deps: AgentDeps | None,
) -> dict[JobKind, Handler]:
    upload_store = store or UploadStore(get_settings().upload_dir)

    async def source_ingest(session: AsyncSession, job: job_service.JobView) -> dict[str, Any]:
        if job.subject_id is None:
            raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "job has no subject")
        if fetcher is None:
            return await source_service.process_source(session, job.subject_id, store=upload_store)
        return await source_service.process_source(
            session, job.subject_id, store=upload_store, fetcher=fetcher
        )

    async def campaign_planning(session: AsyncSession, job: job_service.JobView) -> dict[str, Any]:
        if job.subject_id is None:
            raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "job has no subject")
        if agent_deps is not None:
            return await plan_campaign(
                session, job.subject_id, deps=agent_deps, correlation_id=str(job.id)
            )
        router = build_router(get_settings())
        try:
            deps = AgentDeps(router=router, registry=PromptRegistry.load())
            return await plan_campaign(
                session, job.subject_id, deps=deps, correlation_id=str(job.id)
            )
        finally:
            await router.aclose()

    async def with_content_deps(
        run: Callable[[content_service.ContentDeps], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        settings = get_settings()
        qa = cached_qa_config(settings.config_dir)
        if agent_deps is not None:  # tests: jobs are driven by hand, no queue
            return await run(content_service.ContentDeps(agent=agent_deps, qa=qa))
        router = build_router(settings)
        queue = job_service.RqJobQueue(settings.redis_url.get_secret_value())
        try:
            deps = AgentDeps(router=router, registry=PromptRegistry.load())
            return await run(content_service.ContentDeps(agent=deps, qa=qa, queue=queue))
        finally:
            await router.aclose()

    async def asset_generation(session: AsyncSession, job: job_service.JobView) -> dict[str, Any]:
        if job.subject_id is None:
            raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "job has no subject")
        campaign_id = job.subject_id
        return await with_content_deps(
            lambda deps: content_service.generate_job(session, campaign_id, job.id, deps=deps)
        )

    async def qa_run(session: AsyncSession, job: job_service.JobView) -> dict[str, Any]:
        if job.subject_id is None:
            raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "job has no subject")
        asset_id = job.subject_id

        async def run(deps: content_service.ContentDeps) -> dict[str, Any]:
            status = await content_service.run_asset_qa(
                session, asset_id, deps=deps, correlation_id=str(job.id)
            )
            return {"content_asset_id": str(asset_id), "status": status}

        return await with_content_deps(run)

    async def analytics_pull(session: AsyncSession, job: job_service.JobView) -> dict[str, Any]:
        if job.subject_id is None:
            raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "job has no subject")
        return await analytics_service.pull_campaign_metrics(session, job.subject_id)

    async def recommendation_generation(
        session: AsyncSession, job: job_service.JobView
    ) -> dict[str, Any]:
        if job.subject_id is None:
            raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "job has no subject")
        campaign_id = job.subject_id
        return await with_content_deps(
            lambda deps: recommendation_service.generate(
                session, campaign_id, job.id, deps=deps.agent
            )
        )

    return {
        JobKind.ANALYTICS_PULL: analytics_pull,
        JobKind.RECOMMENDATION_GENERATION: recommendation_generation,
        JobKind.SOURCE_INGEST: source_ingest,
        JobKind.CAMPAIGN_PLANNING: campaign_planning,
        JobKind.ASSET_GENERATION: asset_generation,
        JobKind.QA_RUN: qa_run,
    }


async def _on_failure(
    session: AsyncSession, job: job_service.JobView, exc: OmniGrowthError
) -> None:
    await session.rollback()
    if job.kind is JobKind.SOURCE_INGEST and job.subject_id is not None:
        await source_service.record_failure(session, job.subject_id, exc)
    if job.kind is JobKind.CAMPAIGN_PLANNING and job.subject_id is not None:
        await campaign_service.record_planning_failure(session, job.subject_id, exc, job_id=job.id)
    if job.kind in (JobKind.ASSET_GENERATION, JobKind.RECOMMENDATION_GENERATION):
        # Assets stay DRAFT (STATE_MACHINES §2); only orphaned agent runs are closed.
        await fail_running_agent_runs(session, correlation_id=str(job.id), error_code=exc.code)
    await job_service.mark_failed(session, job, exc)
    await session.commit()


async def execute_job(
    job_id: uuid.UUID,
    *,
    store: UploadStore | None = None,
    fetcher: source_service.Fetcher | None = None,
    agent_deps: AgentDeps | None = None,
) -> JobStatus:
    """Run one job to a terminal state. Returns the final status."""
    async with get_sessionmaker()() as session:
        job = await job_service.get_job(session, job_id)
        with bind_context(correlation_id=str(job_id), campaign_id=None):
            if job.status is not JobStatus.QUEUED:
                log.info("job already processed", extra={"job_id": str(job_id)})
                return job.status
            handler = _handlers(store, fetcher, agent_deps).get(job.kind)
            await job_service.mark_running(session, job)
            await session.commit()
            job = await job_service.get_job(session, job_id)
            if handler is None:
                exc = OmniGrowthError(
                    ErrorCode.QUEUE_FAILURE, f"no handler for job kind {job.kind}"
                )
                await _on_failure(session, job, exc)
                return JobStatus.FAILED
            try:
                result = await handler(session, job)
            except OmniGrowthError as exc:
                log.warning(
                    "job failed", extra={"job_id": str(job_id), "error_code": str(exc.code)}
                )
                await _on_failure(session, job, exc)
                return JobStatus.FAILED
            except _UNEXPECTED as exc:
                # Known infrastructure/library failures must not leave the job RUNNING.
                log.exception("job crashed", extra={"job_id": str(job_id)})
                code = _crash_code(job.kind, exc)
                wrapped = OmniGrowthError(
                    code, "job crashed", details={"reason": type(exc).__name__}
                )
                await _on_failure(session, job, wrapped)
                return JobStatus.FAILED
            await job_service.mark_succeeded(session, job, result)
            await session.commit()
            log.info("job succeeded", extra={"job_id": str(job_id), "kind": str(job.kind)})
            return JobStatus.SUCCEEDED


def run_job(job_id: str) -> str:
    """RQ entrypoint (``omnigrowth.workflows.tasks.run_job``)."""

    async def _run() -> JobStatus:
        try:
            return await execute_job(uuid.UUID(job_id))
        finally:
            await get_engine().dispose()

    return str(aio.run(_run))


# A job RUNNING this long past its RQ time budget (services.jobs) is dead.
STUCK_GRACE = timedelta(seconds=300)


def stuck_after(kind: JobKind) -> timedelta:
    return timedelta(seconds=job_service.job_timeout_s(job_service.queue_for(kind))) + STUCK_GRACE


async def reap_stuck_jobs(now: datetime | None = None) -> int:
    """Fail RUNNING jobs past their kind's time budget (JB6) and their subjects."""
    current = now or datetime.now(UTC)
    cutoff = current - min(stuck_after(k) for k in JobKind)
    reaped = 0
    async with get_sessionmaker()() as session:
        for job in await job_service.stuck_running_jobs(session, cutoff):
            if job.started_at is None or job.started_at > current - stuck_after(job.kind):
                continue  # still inside its own (longer) time budget
            await job_service.reap(session, job)
            if job.kind is JobKind.SOURCE_INGEST and job.subject_id is not None:
                await source_service.record_failure(
                    session,
                    job.subject_id,
                    OmniGrowthError(ErrorCode.QUEUE_FAILURE, "ingest job never finished"),
                )
            if job.kind is JobKind.CAMPAIGN_PLANNING and job.subject_id is not None:
                await campaign_service.record_planning_failure(
                    session,
                    job.subject_id,
                    OmniGrowthError(ErrorCode.QUEUE_FAILURE, "planning job never finished"),
                    job_id=job.id,
                )
            if job.kind in (JobKind.ASSET_GENERATION, JobKind.QA_RUN):
                # Assets stay where they are (DRAFT / GENERATED) and can be re-requested.
                await fail_running_agent_runs(
                    session, correlation_id=str(job.id), error_code=ErrorCode.QUEUE_FAILURE
                )
            await session.commit()
            reaped += 1
            log.warning("reaped stuck job", extra={"job_id": str(job.id), "kind": str(job.kind)})
    return reaped
