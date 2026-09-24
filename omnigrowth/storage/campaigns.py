"""Campaign planning persistence: strategies, research packets/claims, ideas, plan items.

Nothing here commits; the caller owns the transaction. Status columns are written only via
the state machines (``create_with_state`` / ``apply_transition``, AGENTS.md §4).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Table, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import (
    ActorType,
    AgentRunStatus,
    ContentIdeaStatus,
    FunnelStage,
    Platform,
)
from omnigrowth.core.errors import ErrorCode
from omnigrowth.core.ids import uuid7
from omnigrowth.core.state_machines import (
    AGENT_RUN,
    CAMPAIGN,
    CONTENT_IDEA,
    Actor,
    apply_transition,
    create_with_state,
)
from omnigrowth.models import (
    AgentRun,
    AuditEvent,
    Campaign,
    CampaignStrategy,
    ContentIdea,
    PlanItem,
    ResearchClaim,
    ResearchPacket,
)

_IDEAS = cast(Table, ContentIdea.__table__)
_AGENT_RUNS = cast(Table, AgentRun.__table__)
_SYSTEM = Actor(ActorType.SYSTEM, "campaign-planning")
_AUDIT = cast(Table, AuditEvent.__table__)


_CAMPAIGNS = cast(Table, Campaign.__table__)


async def transition_campaign(
    session: AsyncSession,
    *,
    campaign_id: uuid.UUID,
    brand_id: uuid.UUID,
    from_state: str,
    trigger: str,
    actor: Actor,
    correlation_id: str,
    reason: str | None = None,
) -> None:
    await apply_transition(
        session,
        machine=CAMPAIGN,
        table=_CAMPAIGNS,
        audit_table=_AUDIT,
        entity_id=campaign_id,
        from_state=from_state,
        trigger=trigger,
        actor=actor,
        correlation_id=correlation_id,
        brand_id=brand_id,
        reason=reason,
    )


async def get_campaign(
    session: AsyncSession, campaign_id: uuid.UUID, *, lock: bool = False
) -> Campaign | None:
    stmt = select(Campaign).where(Campaign.id == campaign_id)
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ strategy
async def add_strategy(
    session: AsyncSession,
    *,
    campaign: Campaign,
    agent_run_id: uuid.UUID | None,
    strategy: dict[str, Any],
    positioning: str,
    funnel_stage: FunnelStage,
    primary_cta: str | None,
    primary_platform: Platform,
    secondary_platforms: Sequence[Platform],
    actor: Actor,
) -> uuid.UUID:
    """New immutable revision; it becomes the single active one."""
    version = (
        await session.execute(
            select(func.coalesce(func.max(CampaignStrategy.version_no), 0)).where(
                CampaignStrategy.campaign_id == campaign.id
            )
        )
    ).scalar_one()
    await session.execute(
        update(CampaignStrategy)
        .where(CampaignStrategy.campaign_id == campaign.id, CampaignStrategy.is_active.is_(True))
        .values(is_active=False)
    )
    row = CampaignStrategy(
        id=uuid7(),
        brand_id=campaign.brand_id,
        campaign_id=campaign.id,
        version_no=version + 1,
        is_active=True,
        agent_run_id=agent_run_id,
        strategy=strategy,
        positioning=positioning,
        funnel_stage=funnel_stage,
        primary_cta=primary_cta,
        primary_platform=primary_platform,
        secondary_platforms=list(secondary_platforms),
        actor_type=actor.type,
        actor_id=actor.id,
    )
    session.add(row)
    await session.flush([row])
    return row.id


async def active_strategy(session: AsyncSession, campaign_id: uuid.UUID) -> CampaignStrategy | None:
    return (
        await session.execute(
            select(CampaignStrategy).where(
                CampaignStrategy.campaign_id == campaign_id, CampaignStrategy.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()


# ------------------------------------------------------------------ research
async def replace_research_packet(
    session: AsyncSession,
    *,
    campaign: Campaign,
    source_asset_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
    provider: str,
    summary: str,
    claims: Sequence[tuple[int, str, Any, str | None]],
) -> uuid.UUID:
    """Archive the current packet (kept for provenance) and insert a new one with claims.

    ``claims``: ``(ordinal, claim_text, ClaimLabel, source_excerpt)``. Citation columns stay
    NULL — the A5 NullResearchProvider never cites.
    """
    await session.execute(
        update(ResearchPacket)
        .where(ResearchPacket.campaign_id == campaign.id, ResearchPacket.archived_at.is_(None))
        .values(archived_at=datetime.now(UTC))
    )
    packet = ResearchPacket(
        id=uuid7(),
        brand_id=campaign.brand_id,
        campaign_id=campaign.id,
        source_asset_id=source_asset_id,
        agent_run_id=agent_run_id,
        provider=provider,
        summary=summary,
    )
    session.add(packet)
    await session.flush([packet])
    session.add_all(
        ResearchClaim(
            id=uuid7(),
            research_packet_id=packet.id,
            ordinal=ordinal,
            claim_text=text,
            label=label,
            source_excerpt=excerpt,
        )
        for ordinal, text, label, excerpt in claims
    )
    await session.flush()
    return packet.id


async def current_research_packet(
    session: AsyncSession, campaign_id: uuid.UUID
) -> tuple[ResearchPacket, list[ResearchClaim]] | None:
    packet = (
        await session.execute(
            select(ResearchPacket).where(
                ResearchPacket.campaign_id == campaign_id, ResearchPacket.archived_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if packet is None:
        return None
    claims = (
        await session.execute(
            select(ResearchClaim)
            .where(ResearchClaim.research_packet_id == packet.id)
            .order_by(ResearchClaim.ordinal)
        )
    ).scalars()
    return packet, list(claims)


# ------------------------------------------------------------------ ideas
async def add_idea(
    session: AsyncSession,
    *,
    campaign: Campaign,
    agent_run_id: uuid.UUID,
    audience_id: uuid.UUID | None,
    values: dict[str, Any],
    correlation_id: str,
) -> uuid.UUID:
    idea_id = uuid7()
    await create_with_state(
        session,
        machine=CONTENT_IDEA,
        table=_IDEAS,
        audit_table=_AUDIT,
        row={
            "id": idea_id,
            "brand_id": campaign.brand_id,
            "campaign_id": campaign.id,
            "agent_run_id": agent_run_id,
            "audience_id": audience_id,
            **values,
        },
        trigger="create",
        actor=Actor(ActorType.AGENT, "opportunity"),
        correlation_id=correlation_id,
        brand_id=campaign.brand_id,
    )
    return idea_id


async def select_idea(
    session: AsyncSession, *, idea_id: uuid.UUID, brand_id: uuid.UUID, correlation_id: str
) -> None:
    await apply_transition(
        session,
        machine=CONTENT_IDEA,
        table=_IDEAS,
        audit_table=_AUDIT,
        entity_id=idea_id,
        from_state=ContentIdeaStatus.PROPOSED,
        trigger="select",
        actor=Actor(ActorType.SYSTEM, "mix-selection"),
        correlation_id=correlation_id,
        brand_id=brand_id,
        reason="deterministic angle-mix selection",
    )


async def deselect_previous_ideas(
    session: AsyncSession,
    *,
    campaign_id: uuid.UUID,
    brand_id: uuid.UUID,
    keep_run_id: uuid.UUID,
    correlation_id: str,
) -> int:
    """A new plan supersedes the mix chosen by earlier planning runs (CI4, SYSTEM)."""
    rows = (
        await session.execute(
            select(ContentIdea.id).where(
                ContentIdea.campaign_id == campaign_id,
                ContentIdea.status == ContentIdeaStatus.SELECTED,
                ContentIdea.agent_run_id.is_distinct_from(keep_run_id),
            )
        )
    ).scalars()
    count = 0
    for idea_id in rows:
        await apply_transition(
            session,
            machine=CONTENT_IDEA,
            table=_IDEAS,
            audit_table=_AUDIT,
            entity_id=idea_id,
            from_state=ContentIdeaStatus.SELECTED,
            trigger="deselect",
            actor=_SYSTEM,
            correlation_id=correlation_id,
            brand_id=brand_id,
            reason="superseded by a newer planning run",
        )
        count += 1
    return count


async def fail_running_agent_runs(
    session: AsyncSession, *, correlation_id: str, error_code: ErrorCode
) -> int:
    """AR3 for runs a crashed / timed-out / reaped job left RUNNING (same correlation id)."""
    rows = (
        await session.execute(
            select(AgentRun.id, AgentRun.brand_id).where(
                AgentRun.correlation_id == correlation_id,
                AgentRun.status == AgentRunStatus.RUNNING,
            )
        )
    ).all()
    for run_id, brand_id in rows:
        await apply_transition(
            session,
            machine=AGENT_RUN,
            table=_AGENT_RUNS,
            audit_table=_AUDIT,
            entity_id=run_id,
            from_state=AgentRunStatus.RUNNING,
            trigger="fail",
            actor=_SYSTEM,
            correlation_id=correlation_id,
            brand_id=brand_id,
            values={"error_code": error_code, "finished_at": datetime.now(UTC)},
            reason="job ended while the agent was running",
        )
    return len(rows)


async def latest_run_id(
    session: AsyncSession, campaign_id: uuid.UUID, agent_name: str
) -> uuid.UUID | None:
    return (
        await session.execute(
            select(AgentRun.id)
            .where(
                AgentRun.campaign_id == campaign_id,
                AgentRun.agent_name == agent_name,
                AgentRun.status == AgentRunStatus.SUCCEEDED,
            )
            .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_ideas(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    *,
    agent_run_id: uuid.UUID | None,
    statuses: Sequence[ContentIdeaStatus] = (),
) -> list[ContentIdea]:
    stmt = select(ContentIdea).where(ContentIdea.campaign_id == campaign_id)
    if agent_run_id is not None:
        stmt = stmt.where(ContentIdea.agent_run_id == agent_run_id)
    if statuses:
        stmt = stmt.where(ContentIdea.status.in_(statuses))
    return list((await session.execute(stmt.order_by(ContentIdea.id))).scalars())


# ------------------------------------------------------------------ plan
async def replace_plan(
    session: AsyncSession, campaign_id: uuid.UUID, horizon_days: int, rows: Sequence[PlanItem]
) -> None:
    """Replace the campaign's plan for ``horizon_days``.

    ``plan_items`` is unique on (campaign, horizon, ordinal) including archived rows, so the
    previous plan cannot be kept alongside the new one; its source of truth remains the
    planner ``agent_runs.output``. A plan item already referenced by a content asset makes
    the delete fail on its FK (planning never runs once assets exist: C2/C5 only).
    """
    await session.execute(
        delete(PlanItem).where(
            PlanItem.campaign_id == campaign_id, PlanItem.horizon_days == horizon_days
        )
    )
    session.add_all(rows)
    await session.flush()


async def list_plan(
    session: AsyncSession, campaign_id: uuid.UUID, horizon_days: int
) -> list[PlanItem]:
    return list(
        (
            await session.execute(
                select(PlanItem)
                .where(
                    PlanItem.campaign_id == campaign_id,
                    PlanItem.horizon_days == horizon_days,
                    PlanItem.archived_at.is_(None),
                )
                .order_by(PlanItem.ordinal)
            )
        ).scalars()
    )
