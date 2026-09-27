"""Approvals (§37), scheduling into the outbox (A3), publication jobs and reconciliation.

SCHEDULE / PUBLISH_NOW never publish inside the request: they create a PENDING
``publication_jobs`` row (J1) keyed by ``sha256(version:account)`` and move the asset to
SCHEDULED (CA10/CA11). The scheduler process does the rest (publishing/scheduler.py).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import Table, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.content.platform_profiles import validate_settings
from omnigrowth.core.enums import (
    ApprovalActionType,
    CampaignStatus,
    ContentAssetStatus,
    Platform,
    PublicationJobStatus,
    PublicationStatus,
    PublishMode,
    QaOutcome,
    UserRole,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.logging import get_context
from omnigrowth.core.settings import get_settings
from omnigrowth.core.state_machines import (
    CONTENT_ASSET,
    PUBLICATION_JOB,
    Actor,
    create_with_state,
)
from omnigrowth.models import (
    AuditEvent,
    Brand,
    Campaign,
    ContentAsset,
    PlatformAccount,
    Publication,
    PublicationJob,
)
from omnigrowth.observability.audit import record_audit
from omnigrowth.publishing.config import cached_publishing_config
from omnigrowth.publishing.scheduler import (
    RELEASE_CLAIM,
    asset_follows_publication,
    cancel_open_job,
    transition_job,
    transition_publication,
)
from omnigrowth.schemas.content import ContentAssetRead, ContentVersionCreate
from omnigrowth.schemas.publishing import (
    ApprovalActionRead,
    ApprovalActionRequest,
    PlatformAccountCreate,
    PlatformAccountRead,
    PlatformAccountUpdate,
    PublicationJobRead,
    PublicationRead,
    PublicationReconcileRequest,
    ReconcileMethod,
)
from omnigrowth.services import content as content_service
from omnigrowth.services.jobs import JobQueue
from omnigrowth.storage import content as content_repo

_JOBS = cast(Table, PublicationJob.__table__)
_AUDIT = cast(Table, AuditEvent.__table__)


def _correlation() -> str:
    return get_context().get("correlation_id") or uuid7().hex


def _not_found(entity: str) -> OmniGrowthError:
    return OmniGrowthError(ErrorCode.NOT_FOUND, f"{entity} not found")


def idempotency_key(content_version_id: uuid.UUID, platform_account_id: uuid.UUID) -> str:
    """A3 / STATE_MACHINES §3.1: sha256_hex(lower(version) + ':' + lower(account))."""
    raw = f"{str(content_version_id).lower()}:{str(platform_account_id).lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ platform accounts
async def _account_read(session: AsyncSession, account: PlatformAccount) -> PlatformAccountRead:
    uncertain = (
        await session.execute(
            select(Publication.id)
            .where(
                Publication.platform_account_id == account.id,
                Publication.status == PublicationStatus.PUBLICATION_UNCERTAIN,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    read = PlatformAccountRead.model_validate(account)
    # Only the MockPublisher exists before Phase 13: other modes publish nothing (the
    # dispatcher refuses them), so they have no effective publisher.
    mode = get_settings().publish_mode
    effective = PublishMode.MOCK if mode is PublishMode.MOCK else None
    return read.model_copy(
        update={"has_open_uncertain": uncertain is not None, "effective_publisher": effective}
    )


async def create_account(
    session: AsyncSession, actor: Actor, body: PlatformAccountCreate
) -> PlatformAccountRead:
    brand = await session.get(Brand, body.brand_id)
    if brand is None or brand.archived_at is not None:
        raise _not_found("brand")
    account = PlatformAccount(
        id=uuid7(),
        brand_id=brand.id,
        platform=body.platform,
        account_type=body.account_type,
        handle=body.handle,
        display_name=body.display_name,
        timezone=body.timezone,
        live_enabled=False,  # A6: live publishing is never enabled at create time
        settings=validate_settings(body.platform, body.settings),
    )
    session.add(account)
    try:
        await session.flush([account])
    except IntegrityError as exc:
        await session.rollback()
        raise OmniGrowthError(
            ErrorCode.CONFLICT, "this handle is already registered for the platform"
        ) from exc
    await session.commit()
    return await _account_read(session, account)


async def update_account(
    session: AsyncSession, actor: Actor, account_id: uuid.UUID, body: PlatformAccountUpdate
) -> PlatformAccountRead:
    """Handle, display name, timezone and settings; never ``live_enabled`` (A6)."""
    account = await session.get(PlatformAccount, account_id, with_for_update=True)
    if account is None or account.archived_at is not None:
        raise _not_found("platform account")
    changes = body.model_dump(exclude_unset=True)
    if "settings" in changes:
        changes["settings"] = validate_settings(account.platform, body.settings)
    if "handle" in changes and changes["handle"] is None:
        del changes["handle"]
    before = {k: getattr(account, k) for k in changes}
    for key, value in changes.items():
        setattr(account, key, value)
    try:
        await session.flush([account])
    except IntegrityError as exc:
        await session.rollback()
        raise OmniGrowthError(
            ErrorCode.CONFLICT, "this handle is already registered for the platform"
        ) from exc
    await record_audit(
        session,
        actor=actor,
        action="platform_account.updated",
        entity_type="platform_accounts",
        entity_id=account.id,
        brand_id=account.brand_id,
        before=before,
        after=changes,
    )
    await session.commit()
    return await _account_read(session, account)


async def list_accounts(
    session: AsyncSession, brand_id: uuid.UUID | None, platform: Platform | None
) -> list[PlatformAccountRead]:
    stmt = select(PlatformAccount).where(PlatformAccount.archived_at.is_(None))
    if brand_id is not None:
        stmt = stmt.where(PlatformAccount.brand_id == brand_id)
    if platform is not None:
        stmt = stmt.where(PlatformAccount.platform == platform)
    rows = (await session.execute(stmt.order_by(PlatformAccount.created_at))).scalars()
    return [await _account_read(session, a) for a in rows]


async def get_account(session: AsyncSession, account_id: uuid.UUID) -> PlatformAccountRead:
    account = await session.get(PlatformAccount, account_id)
    if account is None:
        raise _not_found("platform account")
    return await _account_read(session, account)


# ------------------------------------------------------------------ approvals (§37)
async def _campaign_is(
    session: AsyncSession, asset: ContentAsset, allowed: tuple[CampaignStatus, ...], guard: str
) -> Campaign:
    campaign = await session.get(Campaign, asset.campaign_id)
    if campaign is None or campaign.status not in allowed:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION,
            "the campaign does not allow this action",
            details={"failed_guard": guard},
        )
    return campaign


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    publication_job_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None  # QA_RUN (EDIT) or ASSET_GENERATION (REGENERATE)
    version_id: uuid.UUID | None = None  # the new version created by EDIT


async def apply_action(
    session: AsyncSession,
    queue: JobQueue,
    actor: Actor,
    asset_id: uuid.UUID,
    body: ApprovalActionRequest,
) -> ActionOutcome:
    """Run one of the seven §37 actions; every one records the actor."""
    action = body.action
    if action is ApprovalActionType.EDIT:
        assert body.edit is not None  # noqa: S101 - enforced by the request schema
        version, qa_job = await content_service.edit_asset(
            session,
            queue,
            actor,
            asset_id,
            ContentVersionCreate(
                based_on_version_id=body.content_version_id,
                comment=body.comment,
                **body.edit.model_dump(exclude_unset=True),
            ),
        )
        return ActionOutcome(job_id=qa_job, version_id=version.id)
    if action is ApprovalActionType.REGENERATE:
        job = await content_service.regenerate_asset(
            session, queue, actor, asset_id, body.content_version_id, body.comment
        )
        return ActionOutcome(job_id=job)
    asset = await content_repo.get_asset(session, asset_id, lock=True)
    if asset is None:
        raise _not_found("content asset")
    correlation_id = _correlation()
    from_status = asset.status.value

    if action is ApprovalActionType.APPROVE:
        await _campaign_is(
            session, asset, (CampaignStatus.ACTIVE, CampaignStatus.PAUSED), "campaign_mutable"
        )
        _same_version(body.content_version_id, asset.current_version_id)
        results = await content_repo.qa_results(session, body.content_version_id)
        if len(results) < 4 or any(r.outcome is QaOutcome.FAIL for r in results):
            raise OmniGrowthError(
                ErrorCode.CONFLICT,
                "only a version that passed all four QA stages can be approved",
                details={"failed_guard": "qa_passed"},
            )
        target = await content_repo.transition_asset(
            session,
            asset,
            trigger="APPROVE",
            actor=actor,
            correlation_id=correlation_id,
            values={"approved_version_id": body.content_version_id},
            fence={"current_version_id": body.content_version_id},
        )
        job_id = None
    elif action in (ApprovalActionType.REJECT, ApprovalActionType.REQUEST_REVISION):
        await _campaign_is(
            session, asset, (CampaignStatus.ACTIVE, CampaignStatus.PAUSED), "campaign_mutable"
        )
        _same_version(body.content_version_id, asset.current_version_id)
        target = await content_repo.transition_asset(
            session,
            asset,
            trigger=action.value,
            actor=actor,
            correlation_id=correlation_id,
            fence={"current_version_id": body.content_version_id},
            reason=body.comment,
        )
        job_id = None
    else:  # SCHEDULE / PUBLISH_NOW
        target, job_id = await _schedule(session, actor, asset, body, correlation_id)

    await content_repo.add_approval_action(
        session,
        asset=asset,
        version_id=body.content_version_id,
        resulting_version_id=None,
        action=action,
        from_status=from_status,
        to_status=target,
        actor=actor,
        correlation_id=correlation_id,
        comment=body.comment,
        scheduled_for=body.scheduled_at if action is ApprovalActionType.SCHEDULE else None,
        platform_account_id=body.platform_account_id,
    )
    await session.commit()
    return ActionOutcome(publication_job_id=job_id)


def _same_version(requested: uuid.UUID, current: uuid.UUID | None) -> None:
    if requested != current:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "the asset changed since this version was reviewed",
            details={"current_version_id": str(current)},
        )


async def _schedule(
    session: AsyncSession,
    actor: Actor,
    asset: ContentAsset,
    body: ApprovalActionRequest,
    correlation_id: str,
) -> tuple[str, uuid.UUID]:
    campaign = await _campaign_is(session, asset, (CampaignStatus.ACTIVE,), "campaign_active")
    CONTENT_ASSET.resolve(asset.status, body.action.value, actor)  # before any insert
    if body.content_version_id != asset.approved_version_id:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "only the approved version can be scheduled",
            details={"approved_version_id": str(asset.approved_version_id)},
        )
    account_id = body.platform_account_id
    account = await session.get(PlatformAccount, account_id) if account_id else None
    if account is None or account.brand_id != asset.brand_id or account.archived_at is not None:
        raise _not_found("platform account")
    if account.platform is not asset.platform:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "the account is for a different platform",
            details={
                "account_platform": account.platform.value,
                "asset_platform": asset.platform.value,
            },
        )
    settings = get_settings()
    if settings.publish_mode is PublishMode.LIVE and not account.live_enabled:
        # §3.3 early reject (guard 2); re-checked authoritatively at dispatch.
        raise OmniGrowthError(
            ErrorCode.LIVE_PUBLISH_BLOCKED,
            "this account is not enabled for live publishing",
            details={"guard": "ACCOUNT_NOT_LIVE_ENABLED"},
        )
    config = cached_publishing_config(settings.config_dir).publishing
    now = datetime.now(UTC)
    if body.action is ApprovalActionType.SCHEDULE:
        due_at = body.scheduled_at
        assert due_at is not None  # noqa: S101 - enforced by the request schema
        if due_at < now - timedelta(seconds=config.schedule_grace_seconds):
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE, "scheduled_at is in the past", details={}
            )
    else:
        due_at = now
    if asset.status is ContentAssetStatus.SCHEDULED:
        # CA12 reschedule: the pending job is cancelled (J9) and a new one queued below;
        # refused while the dispatcher holds it or its outcome is unknown.
        await cancel_open_job(session, asset.id, actor, reason="rescheduled")
    job_id = uuid7()
    try:
        async with session.begin_nested():
            await create_with_state(
                session,
                machine=PUBLICATION_JOB,
                table=_JOBS,
                audit_table=_AUDIT,
                row={
                    "id": job_id,
                    "brand_id": asset.brand_id,
                    "content_asset_id": asset.id,
                    "content_version_id": body.content_version_id,
                    "platform_account_id": account.id,
                    "idempotency_key": idempotency_key(body.content_version_id, account.id),
                    "due_at": due_at,
                    "schedule_timezone": account.timezone or campaign.timezone or "UTC",
                    "correlation_id": correlation_id,
                    "max_attempts": config.max_attempts,
                    "actor_type": actor.type,
                    "actor_id": actor.id,
                },
                trigger="enqueue",
                actor=actor,
                correlation_id=correlation_id,
                brand_id=asset.brand_id,
            )
    except IntegrityError as exc:
        # A3 partial unique index: this version is already queued/published to the account.
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "this version is already scheduled or published to this account",
            details={"guard": "idempotency_key"},
        ) from exc
    target = await content_repo.transition_asset(
        session,
        asset,
        trigger=body.action.value,
        actor=actor,
        correlation_id=correlation_id,
        fence={"approved_version_id": body.content_version_id},
    )
    return target, job_id


async def approval_queue(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID | None,
    campaign_id: uuid.UUID | None,
    status: ContentAssetStatus,
) -> list[ContentAssetRead]:
    return await content_service.list_assets(
        session, brand_id=brand_id, campaign_id=campaign_id, statuses=[status]
    )


async def approval_history(session: AsyncSession, asset_id: uuid.UUID) -> list[ApprovalActionRead]:
    return [
        ApprovalActionRead.model_validate(a)
        for a in await content_repo.approval_history(session, asset_id)
    ]


# ------------------------------------------------------------------ publication jobs
async def get_job(session: AsyncSession, job_id: uuid.UUID) -> PublicationJobRead:
    job = await session.get(PublicationJob, job_id, populate_existing=True)
    if job is None:
        raise _not_found("publication job")
    return PublicationJobRead.model_validate(job)


async def list_jobs(
    session: AsyncSession,
    *,
    campaign_id: uuid.UUID | None,
    statuses: list[PublicationJobStatus],
) -> list[PublicationJobRead]:
    stmt = select(PublicationJob)
    if campaign_id is not None:
        stmt = stmt.join(ContentAsset, ContentAsset.id == PublicationJob.content_asset_id).where(
            ContentAsset.campaign_id == campaign_id
        )
    if statuses:
        stmt = stmt.where(PublicationJob.status.in_(statuses))
    rows = (await session.execute(stmt.order_by(PublicationJob.due_at))).scalars()
    return [PublicationJobRead.model_validate(j) for j in rows]


async def cancel_job(
    session: AsyncSession, actor: Actor, job_id: uuid.UUID, reason: str | None
) -> PublicationJobRead:
    """J9 (+ P3b for a publication left PUBLISHING) and CA13 unschedule."""
    job = (
        await session.execute(
            select(PublicationJob).where(PublicationJob.id == job_id).with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        raise _not_found("publication job")
    if job.status is PublicationJobStatus.UNCERTAIN:
        raise OmniGrowthError(
            ErrorCode.PUBLICATION_UNCERTAIN, "reconcile the uncertain publication first"
        )
    await transition_job(session, job, "cancel", actor, reason=reason)
    await transition_publication(
        session,
        job,
        "cancel",
        actor,
        expect=PublicationStatus.PUBLISHING,
        values={"error": {"reason": "cancelled"}},
    )
    asset = await session.get(ContentAsset, job.content_asset_id, populate_existing=True)
    if asset is not None and asset.status is ContentAssetStatus.SCHEDULED:
        await content_repo.transition_asset(
            session,
            asset,
            trigger="unschedule",
            actor=actor,
            correlation_id=job.correlation_id,
            reason=reason,
        )
    await session.commit()
    return await get_job(session, job_id)


# ------------------------------------------------------------------ publications
async def list_publications(
    session: AsyncSession, *, campaign_id: uuid.UUID | None, statuses: list[PublicationStatus]
) -> list[PublicationRead]:
    stmt = select(Publication)
    if campaign_id is not None:
        stmt = stmt.join(ContentAsset, ContentAsset.id == Publication.content_asset_id).where(
            ContentAsset.campaign_id == campaign_id
        )
    if statuses:
        stmt = stmt.where(Publication.status.in_(statuses))
    rows = (await session.execute(stmt.order_by(Publication.scheduled_at))).scalars()
    return [PublicationRead.model_validate(p) for p in rows]


async def get_publication(session: AsyncSession, publication_id: uuid.UUID) -> PublicationRead:
    pub = await session.get(Publication, publication_id, populate_existing=True)
    if pub is None:
        raise _not_found("publication")
    return PublicationRead.model_validate(pub)


async def reconcile(
    session: AsyncSession,
    actor: Actor,
    publication_id: uuid.UUID,
    body: PublicationReconcileRequest,
) -> PublicationRead:
    """Human reconciliation of PUBLICATION_UNCERTAIN (J12b/P5b or J13/P7b). Absence of
    evidence is never proof: only an explicit OWNER statement fails the job (A3)."""
    pub = (
        await session.execute(
            select(Publication).where(Publication.id == publication_id).with_for_update()
        )
    ).scalar_one_or_none()
    if pub is None:
        raise _not_found("publication")
    if pub.status is not PublicationStatus.PUBLICATION_UNCERTAIN:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION, "only an uncertain publication can be reconciled"
        )
    job = await session.get(PublicationJob, pub.publication_job_id, populate_existing=True)
    assert job is not None  # noqa: S101 - FK
    now = datetime.now(UTC)
    evidence = {
        "reconciled_at": now,
        "reconciled_by_actor_type": actor.type,
        "reconciled_by_actor_id": actor.id,
    }
    if body.method is ReconcileMethod.MANUAL_CONFIRM_PUBLISHED:
        url = body.canonical_url or ""
        if not url.startswith("https://"):
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE, "canonical_url must be an absolute https URL"
            )
        await transition_job(
            session,
            job,
            "human_confirmed_published",
            actor,
            values={"completed_at": now},
            reason=body.note,
        )
        await transition_publication(
            session,
            job,
            "human_confirmed_published",
            actor,
            expect=PublicationStatus.PUBLICATION_UNCERTAIN,
            values={
                **evidence,
                "canonical_url": url,
                "external_post_id": body.external_post_id or pub.external_post_id,
                "published_at": body.published_at or now,
            },
        )
        await asset_follows_publication(session, job, "publication_published", reason="human")
    else:
        if actor.role is not UserRole.OWNER:
            raise OmniGrowthError(
                ErrorCode.FORBIDDEN, "only an OWNER may confirm a post was not published"
            )
        await transition_job(
            session,
            job,
            "human_confirmed_not_published",
            actor,
            values={"completed_at": now},
            reason=body.note,
        )
        await transition_publication(
            session,
            job,
            "human_confirmed_not_published",
            actor,
            expect=PublicationStatus.PUBLICATION_UNCERTAIN,
            values={**evidence, "error": {"reason": "confirmed not published", "note": body.note}},
        )
        await asset_follows_publication(session, job, "publication_failed", reason="human")
    await session.commit()
    return await get_publication(session, publication_id)


__all__ = ["RELEASE_CLAIM"]  # re-exported for tests that craft leases
