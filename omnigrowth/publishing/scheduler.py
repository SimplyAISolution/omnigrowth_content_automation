"""Publication outbox scheduler (A3, STATE_MACHINES §3.4): claim -> dispatch -> record.

* **Claim** (one short txn): due ``PENDING``/``RETRY_WAIT`` rows whose asset is SCHEDULED and
  campaign ACTIVE are locked with ``FOR UPDATE SKIP LOCKED`` and moved to CLAIMED (J2/J3)
  through the state machine, with a fresh ``claim_token`` and lease. Two schedulers racing
  can never claim the same row; the partial unique index on ``idempotency_key`` means there
  is never a second row for the same version + account (JI3).
* **Dispatch start** (committed *before* any I/O): guard 4 (open uncertain post on the
  account -> hold, J7b), the live guard (§3.3), then J4 (``attempt_count + 1``) and the
  ``publications`` row (P1).
* **Call** the publisher outside any transaction.
* **Record** the outcome fenced on ``claim_token`` (§3.5): success J5/P2/CA15; retryable
  J7; proven failure J6/P3/CA14; unknown J8/P6 — never retried automatically.
* **Lease recovery**: expired CLAIMED rows go back to PENDING when nothing was sent (J10)
  and to UNCERTAIN when a dispatch had started (J11).

Every timestamp comparison uses the ``now`` passed in, so tests can freeze the clock.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, Table, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.agents.writers import VersionFields, build_draft
from omnigrowth.core.enums import (
    ActorType,
    ApprovalActionType,
    CampaignStatus,
    ContentAssetStatus,
    Environment,
    EvidenceClass,
    PublicationJobStatus,
    PublicationStatus,
    PublishMode,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.settings import get_settings
from omnigrowth.core.state_machines import (
    CONTENT_ASSET,
    PUBLICATION,
    PUBLICATION_JOB,
    Actor,
    apply_transition,
    create_with_state,
)
from omnigrowth.models import (
    ApprovalAction,
    AuditEvent,
    Campaign,
    ContentAsset,
    ContentVersion,
    PlatformAccount,
    Publication,
    PublicationJob,
)
from omnigrowth.observability.audit import record_audit
from omnigrowth.publishing.base import (
    FailureKind,
    PlatformPublisher,
    PublishError,
    PublishRequest,
)
from omnigrowth.publishing.config import PublishingConfig, backoff_seconds
from omnigrowth.publishing.registry import LiveFactory, LiveUnavailableError
from omnigrowth.storage.db import get_sessionmaker

log = logging.getLogger(__name__)

_JOBS = cast(Table, PublicationJob.__table__)
_PUBS = cast(Table, Publication.__table__)
_ASSETS = cast(Table, ContentAsset.__table__)
_AUDIT = cast(Table, AuditEvent.__table__)
_SYSTEM = Actor(ActorType.SYSTEM, "publication-dispatcher")
# Leaving CLAIMED always clears the claim (JI5).
_RELEASE: dict[str, Any] = {
    "claim_token": None,
    "claimed_by": None,
    "claimed_at": None,
    "lease_expires_at": None,
}


@dataclass
class TickResult:
    claimed: int = 0
    succeeded: int = 0
    retry_wait: int = 0
    failed: int = 0
    uncertain: int = 0
    held: int = 0
    recovered: int = 0
    job_ids: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ transitions
async def _job(
    session: AsyncSession,
    job: PublicationJob,
    trigger: str,
    actor: Actor,
    *,
    from_state: str | None = None,
    values: Mapping[str, Any] | None = None,
    token: uuid.UUID | None = None,
    reason: str | None = None,
    extra: Mapping[str, Any] | None = None,
    conditions: Sequence[ColumnElement[bool]] = (),
) -> None:
    await apply_transition(
        session,
        machine=PUBLICATION_JOB,
        table=_JOBS,
        audit_table=_AUDIT,
        entity_id=job.id,
        from_state=from_state or job.status,
        trigger=trigger,
        actor=actor,
        correlation_id=job.correlation_id,
        brand_id=job.brand_id,
        values=values,
        fence={"claim_token": token} if token is not None else None,
        reason=reason,
        extra_metadata=extra,
        conditions=conditions,
    )


async def _publication(
    session: AsyncSession,
    job: PublicationJob,
    trigger: str,
    actor: Actor,
    *,
    expect: PublicationStatus,
    values: Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> bool:
    """Move the job's publication if it exists in ``expect``; True when it moved."""
    pub = (
        await session.execute(select(Publication).where(Publication.publication_job_id == job.id))
    ).scalar_one_or_none()
    if pub is None or pub.status is not expect:
        return False
    await apply_transition(
        session,
        machine=PUBLICATION,
        table=_PUBS,
        audit_table=_AUDIT,
        entity_id=pub.id,
        from_state=expect,
        trigger=trigger,
        actor=actor,
        correlation_id=job.correlation_id,
        brand_id=job.brand_id,
        values=values,
        reason=reason,
    )
    return True


async def _asset(
    session: AsyncSession, job: PublicationJob, trigger: str, reason: str | None = None
) -> None:
    """CA14 / CA15 / CA13: the asset follows the publication (§3.8)."""
    asset = await session.get(ContentAsset, job.content_asset_id, populate_existing=True)
    if asset is None or asset.status is not ContentAssetStatus.SCHEDULED:
        return
    await apply_transition(
        session,
        machine=CONTENT_ASSET,
        table=_ASSETS,
        audit_table=_AUDIT,
        entity_id=asset.id,
        from_state=ContentAssetStatus.SCHEDULED,
        trigger=trigger,
        actor=_SYSTEM,
        correlation_id=job.correlation_id,
        brand_id=job.brand_id,
        reason=reason,
    )


async def _fail_job(
    session: AsyncSession,
    job: PublicationJob,
    actor: Actor,
    *,
    code: ErrorCode,
    now: datetime,
    detail: dict[str, Any],
    token: uuid.UUID | None,
) -> None:
    """J6 + P3 (if dispatched) + CA14: proven not published."""
    await _job(
        session,
        job,
        "publish_failed",
        actor,
        from_state=PublicationJobStatus.CLAIMED,
        token=token,
        values={
            **_RELEASE,
            "completed_at": now,
            "last_error_code": code,
            "last_error": detail,
        },
        extra=detail,
    )
    await _publication(
        session,
        job,
        "publish_failed",
        actor,
        expect=PublicationStatus.PUBLISHING,
        values={"error": {"code": code.value, **detail}},
    )
    await _asset(session, job, "publication_failed", reason=code.value)


# ------------------------------------------------------------------ claim
async def claim_due(
    now: datetime, *, scheduler_id: str, config: PublishingConfig
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    actor = Actor(ActorType.SCHEDULER, scheduler_id)
    lease = timedelta(seconds=config.scheduler.lease_seconds)
    claimed: list[tuple[uuid.UUID, uuid.UUID]] = []
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(PublicationJob)
                .join(ContentAsset, ContentAsset.id == PublicationJob.content_asset_id)
                .join(Campaign, Campaign.id == ContentAsset.campaign_id)
                .where(
                    PublicationJob.status.in_(
                        [PublicationJobStatus.PENDING, PublicationJobStatus.RETRY_WAIT]
                    ),
                    PublicationJob.due_at <= now,
                    ContentAsset.status == ContentAssetStatus.SCHEDULED,
                    Campaign.status == CampaignStatus.ACTIVE,
                )
                .order_by(PublicationJob.due_at)
                .limit(config.scheduler.batch_size)
                .with_for_update(of=PublicationJob, skip_locked=True)
            )
        ).scalars()
        for job in list(rows):
            token = uuid.uuid4()
            await _job(
                session,
                job,
                "claim",
                actor,
                values={
                    "claim_token": token,
                    "claimed_by": scheduler_id,
                    # The database clock sets and checks leases: scheduler hosts may
                    # disagree about the time, the database is the single referee.
                    "claimed_at": func.now(),
                    "lease_expires_at": func.now() + lease,
                },
            )
            claimed.append((job.id, token))
        await session.commit()
    return claimed


# ------------------------------------------------------------------ dispatch
def _request(
    job: PublicationJob, asset: ContentAsset, version: ContentVersion, handle: str
) -> PublishRequest:
    payload = version.platform_payload or {}
    draft = build_draft(
        asset.platform,
        asset.format,
        VersionFields(
            hook=version.hook or "",
            body=version.body,
            cta=version.cta,
            hashtags=[str(t) for t in payload.get("hashtags", [])],
        ),
    )
    return PublishRequest(
        idempotency_key=job.idempotency_key,
        platform=asset.platform,
        format=asset.format,
        account_handle=handle,
        segments=draft.segments,
        content_version_id=version.id,
        media_requirements=list(version.media_requirements),
    )


def evidence_class(mode: PublishMode, account: PlatformAccount) -> EvidenceClass:
    """A17: fixed on the publication when the dispatch starts, never recomputed."""
    if mode is PublishMode.MOCK:
        return EvidenceClass.SIMULATED
    return EvidenceClass.SANDBOX if account.sandbox else EvidenceClass.REAL


async def human_approved(session: AsyncSession, asset_id: uuid.UUID, version_id: uuid.UUID) -> bool:
    """Live guard 3 (STATE_MACHINES Q6): the approval of this version was made by a person.
    The most recent APPROVE of the version decides; an auto-approval (SYSTEM) never does."""
    actor_type = (
        await session.execute(
            select(ApprovalAction.actor_type)
            .where(
                ApprovalAction.content_asset_id == asset_id,
                ApprovalAction.action == ApprovalActionType.APPROVE,
                ApprovalAction.to_status == ContentAssetStatus.APPROVED,
                func.coalesce(
                    ApprovalAction.resulting_version_id, ApprovalAction.content_version_id
                )
                == version_id,
            )
            .order_by(ApprovalAction.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return actor_type is ActorType.HUMAN


async def _start(
    session: AsyncSession,
    job_id: uuid.UUID,
    token: uuid.UUID,
    now: datetime,
    *,
    publisher: PlatformPublisher,
    config: PublishingConfig,
    actor: Actor,
    result: TickResult,
    live_factory: LiveFactory | None = None,
) -> tuple[PublishRequest, PlatformPublisher, float] | None:
    """Guards + J4 + P1, committed before the call. None = nothing to send."""
    job = await session.get(PublicationJob, job_id, populate_existing=True)
    if job is None or job.status is not PublicationJobStatus.CLAIMED or job.claim_token != token:
        return None
    request_timeout = timedelta(seconds=config.scheduler.request_timeout_seconds)
    if job.lease_expires_at is None:
        return None
    asset = await session.get(ContentAsset, job.content_asset_id)
    account = await session.get(PlatformAccount, job.platform_account_id)
    version = await session.get(ContentVersion, job.content_version_id)
    if asset is None or account is None or version is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "publication job references missing rows")

    # Guard 4 (MOCK and LIVE): an uncertain post on this account holds every dispatch.
    open_uncertain = (
        await session.execute(
            select(Publication.id)
            .where(
                Publication.platform_account_id == account.id,
                Publication.status == PublicationStatus.PUBLICATION_UNCERTAIN,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if open_uncertain is not None:
        await _job(
            session,
            job,
            "blocked_by_uncertain",
            actor,
            token=token,
            values={
                **_RELEASE,
                "due_at": now + timedelta(seconds=config.publishing.uncertain_hold_seconds),
                "last_error_code": ErrorCode.LIVE_PUBLISH_BLOCKED,
                "last_error": {"guard": "OPEN_UNCERTAIN"},
            },
            extra={"guard": "OPEN_UNCERTAIN"},
        )
        await session.commit()
        result.held += 1
        return None

    settings = get_settings()
    mode = settings.publish_mode
    refusal: str | None = None
    use: PlatformPublisher = publisher
    if account.brand_id != job.brand_id or asset.brand_id != job.brand_id:
        # Brand isolation (every mode): a job only ever posts with an account of its brand.
        refusal = "BRAND_MISMATCH"
    elif mode is PublishMode.LIVE:
        # §3.3 authoritative re-check; the ENV=test block is independent of settings validation.
        if settings.env is Environment.TEST:
            refusal = "ENV_TEST"
        elif not account.live_enabled:
            refusal = "ACCOUNT_NOT_LIVE_ENABLED"
        elif asset.approved_version_id != job.content_version_id:
            refusal = "VERSION_NOT_APPROVED"
        elif not await human_approved(session, asset.id, job.content_version_id):
            refusal = "NOT_HUMAN_APPROVED"
        elif live_factory is None:
            refusal = "NO_VERIFIED_ADAPTER"
        else:
            try:
                use = await live_factory(session, account)
            except LiveUnavailableError as exc:
                refusal = exc.reason  # NO_VERIFIED_ADAPTER, NO_CREDENTIAL, ...
    elif mode is not publisher.mode:
        await _fail_job(
            session,
            job,
            actor,
            code=ErrorCode.CONFIGURATION_ERROR,
            now=now,
            detail={"reason": f"no {mode.value} publisher is available"},
            token=token,
        )
        await session.commit()
        result.failed += 1
        return None
    if refusal is not None:
        await _fail_job(
            session,
            job,
            actor,
            code=ErrorCode.LIVE_PUBLISH_BLOCKED,
            now=now,
            detail={"guard": refusal},
            token=token,
        )
        await session.commit()
        result.failed += 1
        return None

    request = _request(job, asset, version, account.handle)
    try:
        use.validate(request)
    except PublishError as exc:
        await _fail_job(
            session, job, actor, code=exc.code, now=now, detail={"reason": str(exc)}, token=token
        )
        await session.commit()
        result.failed += 1
        return None

    # A17: a retry must be made the way the first attempt was recorded. If the publish mode
    # or the account's sandbox flag changed since (and nothing was proven sent), the
    # publication's evidence would lie: refuse and let a person schedule it again.
    evidence = evidence_class(use.mode, account)
    earlier = (
        await session.execute(
            select(Publication.publish_mode, Publication.evidence_class).where(
                Publication.publication_job_id == job.id
            )
        )
    ).one_or_none()
    if earlier is not None and (earlier.publish_mode, earlier.evidence_class) != (
        use.mode,
        evidence,
    ):
        detail = {
            "guard": "MODE_CHANGED",
            "recorded": f"{earlier.publish_mode.value}/{earlier.evidence_class.value}",
            "now": f"{use.mode.value}/{evidence.value}",
        }
        await _fail_job(
            session,
            job,
            actor,
            code=ErrorCode.LIVE_PUBLISH_BLOCKED,
            now=now,
            detail=detail,
            token=token,
        )
        await session.commit()
        result.failed += 1
        return None

    # One budget for the whole send (a thread is several sequential requests); the lease
    # is extended to cover it, and dispatch_one stops the send when it runs out.
    budget = timedelta(
        seconds=config.scheduler.request_timeout_seconds * max(1, len(request.segments)) + 15
    )

    await _job(
        session,
        job,
        "dispatch_start",
        actor,
        token=token,
        values={
            "dispatch_started_at": now,
            "attempt_count": job.attempt_count + 1,
            "publish_mode": use.mode,
            "lease_expires_at": func.greatest(_JOBS.c.lease_expires_at, func.now() + budget),
        },
        # §3.4 step 2: the database decides whether enough lease is left (its clock, not
        # the tick's start time), so a slow batch can never send on an expired lease.
        conditions=[_JOBS.c.lease_expires_at > func.now() + request_timeout],
    )
    exists = (
        await session.execute(
            select(Publication.id).where(Publication.publication_job_id == job.id)
        )
    ).scalar_one_or_none()
    if exists is None:
        await create_with_state(
            session,
            machine=PUBLICATION,
            table=_PUBS,
            audit_table=_AUDIT,
            row={
                "id": uuid7(),
                "brand_id": job.brand_id,
                "publication_job_id": job.id,
                "content_asset_id": job.content_asset_id,
                "content_version_id": job.content_version_id,
                "platform_account_id": job.platform_account_id,
                "platform": asset.platform,
                "publish_mode": use.mode,
                "evidence_class": evidence,
                "scheduled_at": job.due_at,
            },
            trigger="dispatch_start",
            actor=actor,
            correlation_id=job.correlation_id,
            brand_id=job.brand_id,
        )
    await session.commit()
    return request, use, budget.total_seconds() - 5


async def _record_success(
    session: AsyncSession,
    job_id: uuid.UUID,
    token: uuid.UUID,
    now: datetime,
    outcome: Any,
    actor: Actor,
    result: TickResult,
) -> None:
    job = await session.get(PublicationJob, job_id, populate_existing=True)
    assert job is not None  # noqa: S101 - created before dispatch, never deleted
    pub_values = {
        "external_post_id": outcome.external_post_id,
        "canonical_url": outcome.canonical_url,
        "published_at": outcome.published_at,
    }
    try:
        await _job(
            session,
            job,
            "publish_succeeded",
            actor,
            from_state=PublicationJobStatus.CLAIMED,
            token=token,
            values={**_RELEASE, "completed_at": now, "last_error_code": None, "last_error": None},
        )
        await _publication(
            session,
            job,
            "publish_succeeded",
            actor,
            expect=PublicationStatus.PUBLISHING,
            values=pub_values,
        )
    except OmniGrowthError as exc:
        if exc.code is not ErrorCode.CONFLICT:
            raise
        # §3.4 step 4: the lease was recovered meanwhile (J11) but we hold the evidence.
        await session.rollback()
        job = await session.get(PublicationJob, job_id, populate_existing=True)
        if job is None or job.status is not PublicationJobStatus.UNCERTAIN:
            await _late_result(session, job_id, "success", pub_values)
            return
        await _job(
            session,
            job,
            "reconciled_found",
            _SYSTEM,
            values={"completed_at": now, "last_error_code": None, "last_error": None},
            reason="late success from the original dispatch",
        )
        await _publication(
            session,
            job,
            "reconciled_found",
            _SYSTEM,
            expect=PublicationStatus.PUBLICATION_UNCERTAIN,
            values={
                **pub_values,
                "reconciled_at": now,
                "reconciled_by_actor_type": ActorType.SYSTEM,
                "reconciled_by_actor_id": _SYSTEM.id,
            },
        )
    await _asset(session, job, "publication_published")
    await session.commit()
    result.succeeded += 1


async def _record_failure(
    session: AsyncSession,
    job_id: uuid.UUID,
    token: uuid.UUID,
    now: datetime,
    exc: PublishError,
    *,
    config: PublishingConfig,
    actor: Actor,
    result: TickResult,
) -> None:
    job = await session.get(PublicationJob, job_id, populate_existing=True)
    assert job is not None  # noqa: S101 - see _record_success
    detail = {"reason": str(exc), "kind": exc.kind.value}
    if exc.uncertain:
        # Outcome unknown (§3.5): never retried; blocks the account until reconciled.
        await _job(
            session,
            job,
            "publish_uncertain",
            actor,
            from_state=PublicationJobStatus.CLAIMED,
            token=token,
            values={**_RELEASE, "last_error_code": exc.code, "last_error": detail},
            extra=detail,
        )
        await _publication(
            session,
            job,
            "publish_uncertain",
            actor,
            expect=PublicationStatus.PUBLISHING,
            values={"error": {"code": exc.code.value, **detail}},
        )
        result.uncertain += 1
    elif exc.retryable and job.attempt_count < job.max_attempts:
        delay = backoff_seconds(
            job.attempt_count, config.publishing, retry_after_s=exc.retry_after_s
        )
        await _job(
            session,
            job,
            "publish_retryable",
            actor,
            from_state=PublicationJobStatus.CLAIMED,
            token=token,
            values={
                **_RELEASE,
                "due_at": now + timedelta(seconds=delay),
                "last_error_code": exc.code,
                "last_error": detail,
            },
            extra=detail,
        )
        result.retry_wait += 1
    else:
        await _fail_job(session, job, actor, code=exc.code, now=now, detail=detail, token=token)
        result.failed += 1
    await session.commit()


async def dispatch_one(
    job_id: uuid.UUID,
    token: uuid.UUID,
    now: datetime,
    *,
    publisher: PlatformPublisher,
    config: PublishingConfig,
    scheduler_id: str,
    result: TickResult,
    live_factory: LiveFactory | None = None,
) -> None:
    actor = Actor(ActorType.SCHEDULER, scheduler_id)
    try:
        async with get_sessionmaker()() as session:
            started = await _start(
                session,
                job_id,
                token,
                now,
                publisher=publisher,
                config=config,
                actor=actor,
                result=result,
                live_factory=live_factory,
            )
    except OmniGrowthError as exc:
        if exc.code is not ErrorCode.CONFLICT:
            raise
        # Lease lost / job moved before dispatch started: nothing was sent.
        log.warning("dispatch start lost its claim", extra={"job_id": str(job_id)})
        return
    if started is None:
        return
    request, use, budget_s = started
    try:
        try:
            async with asyncio.timeout(budget_s):
                outcome = await use.publish(request)  # outside any transaction
        except TimeoutError as exc:
            # Stopped mid-send: the platform may have acted on it. Never retried (A3).
            raise PublishError(
                FailureKind.AFTER_SEND, "the send ran out of its time budget"
            ) from exc
    except PublishError as exc:
        log.warning(
            "publish failed",
            extra={"job_id": str(job_id), "kind": exc.kind.value, "code": exc.code.value},
        )
        try:
            async with get_sessionmaker()() as session:
                await _record_failure(
                    session, job_id, token, now, exc, config=config, actor=actor, result=result
                )
        except OmniGrowthError as conflict:
            if conflict.code is not ErrorCode.CONFLICT:
                raise
            # The job moved on meanwhile (e.g. J11 -> UNCERTAIN): keep the evidence.
            async with get_sessionmaker()() as session:
                await _late_result(
                    session, job_id, "failure", {"kind": exc.kind.value, "code": exc.code.value}
                )
        return
    async with get_sessionmaker()() as session:
        await _record_success(session, job_id, token, now, outcome, actor, result)


async def _late_result(
    session: AsyncSession, job_id: uuid.UUID, outcome: str, evidence: Mapping[str, Any]
) -> None:
    """A publish result arrived after the job left CLAIMED (lease recovered, human decision).
    No transition is possible or safe; the evidence goes to the audit log for reconciliation."""
    await session.rollback()
    job = await session.get(PublicationJob, job_id, populate_existing=True)
    log.warning(
        "late publish result for a job that moved on",
        extra={"job_id": str(job_id), "outcome": outcome},
    )
    await record_audit(
        session,
        actor=_SYSTEM,
        action="publication.late_result",
        entity_type="publication_jobs",
        entity_id=job_id,
        brand_id=job.brand_id if job else None,
        metadata={
            "outcome": outcome,
            "job_status": job.status.value if job else None,
            "evidence": {k: str(v) for k, v in evidence.items()},
        },
    )
    await session.commit()


# ------------------------------------------------------------------ lease recovery
async def recover_expired_leases(
    now: datetime, *, publisher: PlatformPublisher, scheduler_id: str, result: TickResult
) -> None:
    actor = Actor(ActorType.SCHEDULER, scheduler_id)
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(PublicationJob)
                .where(
                    PublicationJob.status == PublicationJobStatus.CLAIMED,
                    # ``now`` may be ahead of the DB clock in tests (frozen clock), never
                    # behind it in production: expired by either clock counts.
                    PublicationJob.lease_expires_at < func.greatest(now, func.now()),
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars()
        for job in list(rows):
            dispatched_with_side_effect = job.publish_mode is not PublishMode.MANUAL
            if job.dispatch_started_at is None or not dispatched_with_side_effect:
                await _job(
                    session,
                    job,
                    "lease_expired_not_dispatched",
                    actor,
                    values={**_RELEASE, "dispatch_started_at": None},
                )
            else:
                await _job(
                    session,
                    job,
                    "lease_expired_dispatched",
                    actor,
                    values={**_RELEASE, "last_error_code": ErrorCode.PUBLICATION_UNCERTAIN},
                )
                await _publication(
                    session,
                    job,
                    "lease_expired_dispatched",
                    actor,
                    expect=PublicationStatus.PUBLISHING,
                    values={
                        "error": {
                            "code": ErrorCode.PUBLICATION_UNCERTAIN.value,
                            "reason": "lease expired after dispatch",
                        }
                    },
                )
                result.uncertain += 1
            result.recovered += 1
        await session.commit()


async def run_once(
    *,
    publisher: PlatformPublisher,
    config: PublishingConfig,
    now: datetime,
    scheduler_id: str = "scheduler",
    live_factory: LiveFactory | None = None,
) -> TickResult:
    """One scheduler tick: recover leases, claim due jobs, dispatch each."""
    result = TickResult()
    await recover_expired_leases(now, publisher=publisher, scheduler_id=scheduler_id, result=result)
    claimed = await claim_due(now, scheduler_id=scheduler_id, config=config)
    result.claimed = len(claimed)
    for job_id, token in claimed:
        result.job_ids.append(str(job_id))
        await dispatch_one(
            job_id,
            token,
            now,
            publisher=publisher,
            config=config,
            scheduler_id=scheduler_id,
            result=result,
            live_factory=live_factory,
        )
    return result


# Shared with the API-side services (cancel, human reconciliation): one implementation of
# "job + publication + asset move together" (§3.8).
transition_job = _job
build_request = _request
transition_publication = _publication
asset_follows_publication = _asset
RELEASE_CLAIM = _RELEASE


async def cancel_open_job(
    session: AsyncSession, asset_id: uuid.UUID, actor: Actor, reason: str | None
) -> PublicationJob | None:
    """J9 (+ P3b) for the asset's pending job, before EDIT/REGENERATE/reschedule (X7).

    Refuses when the job is already with the dispatcher (CLAIMED) or its outcome is unknown
    (UNCERTAIN): the post may exist, so the asset must not change underneath it.
    """
    job = (
        await session.execute(
            select(PublicationJob)
            .where(
                PublicationJob.content_asset_id == asset_id,
                PublicationJob.status.in_(
                    [
                        PublicationJobStatus.PENDING,
                        PublicationJobStatus.RETRY_WAIT,
                        PublicationJobStatus.CLAIMED,
                        PublicationJobStatus.UNCERTAIN,
                    ]
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        return None
    if job.status is PublicationJobStatus.UNCERTAIN:
        raise OmniGrowthError(
            ErrorCode.PUBLICATION_UNCERTAIN, "reconcile the uncertain publication first"
        )
    if job.status is PublicationJobStatus.CLAIMED:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION,
            "the post is being published right now; try again shortly",
            details={"failed_guard": "job_cancellable"},
        )
    await _job(session, job, "cancel", actor, reason=reason)
    await _publication(
        session,
        job,
        "cancel",
        actor,
        expect=PublicationStatus.PUBLISHING,
        values={"error": {"reason": "cancelled"}},
    )
    return job
