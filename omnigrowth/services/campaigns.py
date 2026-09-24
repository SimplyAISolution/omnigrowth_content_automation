"""Campaigns (API_PLAN §2.5): create / wizard, planning job, activation and planning reads.

Planning itself runs in the worker (``campaigns.orchestrator`` via a CAMPAIGN_PLANNING job);
this module only moves the campaign into PLANNING (C2/C5), out of it on failure (C4) and on
to ACTIVE (C7). Status changes go through the CAMPAIGN state machine.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from typing import cast

from sqlalchemy import Table, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import (
    ActorType,
    AngleType,
    CampaignStatus,
    ContentIdeaStatus,
    JobKind,
    Platform,
    SourceAssetStatus,
    SourceType,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.logging import get_context
from omnigrowth.core.state_machines import CAMPAIGN, Actor, create_with_state
from omnigrowth.models import (
    Audience,
    AuditEvent,
    Brand,
    Campaign,
    CampaignSource,
    Offer,
    SourceAsset,
)
from omnigrowth.schemas.campaigns import (
    CampaignCreate,
    CampaignRead,
    CampaignStrategyRead,
    CampaignWizardRequest,
    ContentIdeaRead,
    PlanItemRead,
    ResearchClaimRead,
    ResearchPacketRead,
)
from omnigrowth.services import sources as source_service
from omnigrowth.services.jobs import JobQueue, cancel_queued, create_job, dispatch
from omnigrowth.storage import campaigns as repo

_CAMPAIGNS = cast(Table, Campaign.__table__)
_AUDIT = cast(Table, AuditEvent.__table__)
_SYSTEM = Actor(ActorType.SYSTEM, "campaign-planning")
PLAN_HORIZON_DAYS = 7


def _correlation() -> str:
    return get_context().get("correlation_id") or uuid7().hex


def _not_found(entity: str) -> OmniGrowthError:
    return OmniGrowthError(ErrorCode.NOT_FOUND, f"{entity} not found")


async def _require_campaign(
    session: AsyncSession, campaign_id: uuid.UUID, *, lock: bool = False
) -> Campaign:
    campaign = await repo.get_campaign(session, campaign_id, lock=lock)
    if campaign is None:
        raise _not_found("campaign")
    return campaign


async def _check_refs(session: AsyncSession, body: CampaignCreate) -> Brand:
    brand = await session.get(Brand, body.brand_id)
    if brand is None or brand.archived_at is not None:
        raise _not_found("brand")
    if body.audience_id is not None:
        audience = await session.get(Audience, body.audience_id)
        if audience is None or audience.brand_id != brand.id or audience.archived_at:
            raise _not_found("audience")
    if body.offer_id is not None:
        offer = await session.get(Offer, body.offer_id)
        if offer is None or offer.brand_id != brand.id or offer.archived_at:
            raise _not_found("offer")
    if body.source_asset_ids:
        found = set(
            (
                await session.execute(
                    select(SourceAsset.id).where(
                        SourceAsset.id.in_(body.source_asset_ids),
                        SourceAsset.brand_id == brand.id,
                        SourceAsset.archived_at.is_(None),
                    )
                )
            ).scalars()
        )
        missing = [str(s) for s in body.source_asset_ids if s not in found]
        if missing:
            raise OmniGrowthError(
                ErrorCode.NOT_FOUND, "source not found", details={"source_asset_ids": missing}
            )
    return brand


# ------------------------------------------------------------------ create / read
async def create_campaign(
    session: AsyncSession, actor: Actor, body: CampaignCreate, *, commit: bool = True
) -> CampaignRead:
    """Manual create -> DRAFT (C1), linking ``source_asset_ids`` in order."""
    brand = await _check_refs(session, body)
    campaign_id = uuid7()
    await create_with_state(
        session,
        machine=CAMPAIGN,
        table=_CAMPAIGNS,
        audit_table=_AUDIT,
        row={
            "id": campaign_id,
            "brand_id": brand.id,
            "name": body.name,
            "objective": body.objective,
            "topic": body.topic,
            "primary_platform": body.primary_platform,
            "secondary_platforms": list(body.secondary_platforms),
            "audience_id": body.audience_id,
            "offer_id": body.offer_id,
            "content_pillar": body.content_pillar,
            "priority": body.priority,
            "start_date": body.start_date,
            "end_date": body.end_date,
            "timezone": body.timezone,
            "routing_profile": body.routing_profile or brand.default_routing_profile,
            "budget_limit_usd": body.budget_limit_usd,
            "actor_type": actor.type,
            "actor_id": actor.id,
        },
        trigger="create",
        actor=actor,
        correlation_id=_correlation(),
        brand_id=brand.id,
    )
    session.add_all(
        CampaignSource(campaign_id=campaign_id, source_asset_id=source_id, position=position)
        for position, source_id in enumerate(body.source_asset_ids)
    )
    await session.flush()
    if commit:
        await session.commit()
    return await get_campaign(session, campaign_id)


async def get_campaign(session: AsyncSession, campaign_id: uuid.UUID) -> CampaignRead:
    campaign = await _require_campaign(session, campaign_id)
    await session.refresh(campaign)
    return CampaignRead.model_validate(campaign)


async def list_campaigns(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID | None,
    statuses: Sequence[CampaignStatus] = (),
    limit: int = 100,
) -> list[CampaignRead]:
    stmt = select(Campaign).where(Campaign.archived_at.is_(None))
    if brand_id is not None:
        stmt = stmt.where(Campaign.brand_id == brand_id)
    if statuses:
        stmt = stmt.where(Campaign.status.in_(statuses))
    rows = (await session.execute(stmt.order_by(Campaign.created_at.desc()).limit(limit))).scalars()
    return [CampaignRead.model_validate(r) for r in rows]


# ------------------------------------------------------------------ planning lifecycle
async def _has_source(session: AsyncSession, campaign_id: uuid.UUID) -> bool:
    count = (
        await session.execute(
            select(func.count())
            .select_from(CampaignSource)
            .join(SourceAsset, SourceAsset.id == CampaignSource.source_asset_id)
            .where(
                CampaignSource.campaign_id == campaign_id,
                SourceAsset.status == SourceAssetStatus.NORMALIZED,
            )
        )
    ).scalar_one()
    return bool(count)


async def start_planning(
    session: AsyncSession, queue: JobQueue, actor: Actor, campaign_id: uuid.UUID
) -> uuid.UUID:
    """DRAFT -> PLANNING (C2) or FAILED -> PLANNING (C5), then enqueue CAMPAIGN_PLANNING."""
    campaign = await _require_campaign(session, campaign_id, lock=True)
    from_state = campaign.status
    trigger = "retry_planning" if from_state is CampaignStatus.FAILED else "start_planning"
    CAMPAIGN.resolve(from_state, trigger, actor)  # structural check before the guard
    if not await _has_source(session, campaign_id):
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION,
            "campaign needs at least one normalized source before planning",
            details={"failed_guard": "has_source"},
        )
    correlation_id = _correlation()
    await repo.transition_campaign(
        session,
        campaign_id=campaign_id,
        brand_id=campaign.brand_id,
        from_state=from_state,
        trigger=trigger,
        actor=actor,
        correlation_id=correlation_id,
    )
    job_id = await create_job(
        session,
        kind=JobKind.CAMPAIGN_PLANNING,
        actor=actor,
        brand_id=campaign.brand_id,
        subject_type="campaigns",
        subject_id=campaign_id,
    )
    await session.commit()  # the row must exist before a worker can pick the job up
    try:
        dispatch(queue, JobKind.CAMPAIGN_PLANNING, job_id)
    except OmniGrowthError as exc:
        # Nothing will ever run this job: close it and leave the campaign retryable (C5).
        await cancel_queued(session, job_id, reason="queue unavailable")
        await record_planning_failure(session, campaign_id, exc)
        await session.commit()
        raise
    return job_id


async def record_planning_failure(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    exc: OmniGrowthError,
    *,
    job_id: uuid.UUID | None = None,
) -> None:
    """PLANNING -> FAILED (C4), failing any agent run the job left RUNNING (AR3).

    No-op for the campaign if it already left PLANNING.
    """
    if job_id is not None:
        await repo.fail_running_agent_runs(session, correlation_id=str(job_id), error_code=exc.code)
    campaign = await repo.get_campaign(session, campaign_id, lock=True)
    if campaign is None or campaign.status is not CampaignStatus.PLANNING:
        return
    await repo.transition_campaign(
        session,
        campaign_id=campaign_id,
        brand_id=campaign.brand_id,
        from_state=CampaignStatus.PLANNING,
        trigger="planning_failed",
        actor=_SYSTEM,
        correlation_id=_correlation(),
        reason=str(exc.code),
    )


async def activate(session: AsyncSession, actor: Actor, campaign_id: uuid.UUID) -> CampaignRead:
    """READY_FOR_REVIEW -> ACTIVE (C7, ``accept_plan``). Does not generate assets."""
    campaign = await _require_campaign(session, campaign_id, lock=True)
    await repo.transition_campaign(
        session,
        campaign_id=campaign_id,
        brand_id=campaign.brand_id,
        from_state=campaign.status,
        trigger="accept_plan",
        actor=actor,
        correlation_id=_correlation(),
    )
    await session.commit()
    return await get_campaign(session, campaign_id)


async def run_wizard(
    session: AsyncSession, queue: JobQueue, actor: Actor, body: CampaignWizardRequest
) -> tuple[CampaignRead, uuid.UUID | None, uuid.UUID | None]:
    """§39: optional inline source, campaign DRAFT, optional planning job."""
    source_id: uuid.UUID | None = None
    create = CampaignCreate.model_validate(
        body.model_dump(exclude={"source_text", "source_title", "generate"})
    )
    # Validate every reference before anything is committed (no orphan source on 404).
    await _check_refs(session, create)
    if body.source_text:
        accepted = await source_service.ingest_text(
            session,
            actor=actor,
            brand_id=body.brand_id,
            source_type=SourceType.TEXT,
            content=body.source_text,
            title=body.source_title,
        )
        source_id = accepted.source.id
        if source_id not in create.source_asset_ids:
            create.source_asset_ids = [*create.source_asset_ids, source_id]
    campaign = await create_campaign(session, actor, create)
    job_id = await start_planning(session, queue, actor, campaign.id) if body.generate else None
    return await get_campaign(session, campaign.id), source_id, job_id


# ------------------------------------------------------------------ planning outputs
async def get_strategy(session: AsyncSession, campaign_id: uuid.UUID) -> CampaignStrategyRead:
    await _require_campaign(session, campaign_id)
    row = await repo.active_strategy(session, campaign_id)
    if row is None:
        raise _not_found("strategy")
    return CampaignStrategyRead.model_validate(row)


async def get_research_packet(session: AsyncSession, campaign_id: uuid.UUID) -> ResearchPacketRead:
    await _require_campaign(session, campaign_id)
    found = await repo.current_research_packet(session, campaign_id)
    if found is None:
        raise _not_found("research packet")
    packet, claims = found
    return ResearchPacketRead(
        id=packet.id,
        campaign_id=packet.campaign_id,
        source_asset_id=packet.source_asset_id,
        agent_run_id=packet.agent_run_id,
        provider=packet.provider,
        summary=packet.summary,
        created_at=packet.created_at,
        claim_counts=dict(Counter(c.label for c in claims)),
        claims=[ResearchClaimRead.model_validate(c) for c in claims],
    )


async def list_ideas(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    *,
    statuses: Sequence[ContentIdeaStatus] = (),
    angles: Sequence[AngleType] = (),
    platforms: Sequence[Platform] = (),
) -> list[ContentIdeaRead]:
    """Ideas from the latest opportunity run (older runs stay for provenance)."""
    await _require_campaign(session, campaign_id)
    run_id = await repo.latest_run_id(session, campaign_id, "opportunity")
    if run_id is None:
        return []
    rows = await repo.list_ideas(session, campaign_id, agent_run_id=run_id, statuses=statuses)
    return [
        ContentIdeaRead.model_validate(r)
        for r in rows
        if (not angles or r.angle in angles)
        and (not platforms or set(platforms) & set(r.platform_targets))
    ]


async def get_plan(
    session: AsyncSession, campaign_id: uuid.UUID, horizon_days: int = PLAN_HORIZON_DAYS
) -> list[PlanItemRead]:
    await _require_campaign(session, campaign_id)
    rows = await repo.list_plan(session, campaign_id, horizon_days)
    return [PlanItemRead.model_validate(r) for r in rows]
