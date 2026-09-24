"""Campaign planning pipeline (§7A-D, API_PLAN ``/campaigns/{id}/generate``).

research (A5) -> strategy -> opportunities (>=20, every AngleType) -> deterministic mix
selection -> 7-day plan. Each step commits its own rows, so a failure leaves the earlier
steps inspectable. The campaign moves PLANNING -> READY_FOR_REVIEW (C3) here; the failure
edge (C4) is taken by the job runner (``services.campaigns.record_planning_failure``), which
also covers crashes this module never sees. Never generates assets.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.agents import opportunity, planner, research, strategy
from omnigrowth.agents.base import AgentDeps, AgentScope, run_agent
from omnigrowth.campaigns.context import PlanningContext, load_planning_context
from omnigrowth.core.enums import ActorType, CampaignStatus
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.state_machines import Actor
from omnigrowth.models import PlanItem
from omnigrowth.storage import campaigns as repo

log = logging.getLogger(__name__)

# Ideas taken into the plan: one per day of the 7-day horizon, spread across angles.
PLAN_MIX_SIZE = planner.HORIZON_DAYS
_STRATEGY_ACTOR = Actor(ActorType.AGENT, strategy.SPEC.name)
_PLANNER_SYSTEM = Actor(ActorType.SYSTEM, "campaign-planning")


def _start_date(ctx: PlanningContext, today: date | None) -> date:
    if ctx.start_date is not None:
        return ctx.start_date
    return today or datetime.now(ZoneInfo(ctx.timezone)).date()


async def plan_campaign(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    *,
    deps: AgentDeps,
    correlation_id: str,
    today: date | None = None,
) -> dict[str, Any]:
    """Run the pipeline for a PLANNING campaign and return a job-result summary."""
    campaign = await repo.get_campaign(session, campaign_id)
    if campaign is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "campaign not found")
    if campaign.status is not CampaignStatus.PLANNING:
        return {"campaign_id": str(campaign_id), "skipped": f"status {campaign.status}"}

    ctx = await load_planning_context(session, campaign_id)
    scope = AgentScope(
        brand_id=ctx.brand_id,
        campaign_id=ctx.campaign_id,
        correlation_id=correlation_id,
        routing_profile=ctx.routing_profile,
        brand_profile_id=ctx.brand_profile_id,
    )
    base_ref = {"campaign_id": str(campaign_id), "source_asset_id": str(ctx.source_asset_id)}

    # 1. Research (A5): claims extracted from the source, provenance-checked.
    res = await run_agent(
        session,
        research.SPEC,
        research.variables(
            brand_block=ctx.brand_block,
            campaign_block=ctx.campaign_block,
            source_text=ctx.source_text,
        ),
        deps=deps,
        scope=scope,
        input_ref=base_ref,
    )
    claims = research.verify_claims(res.value, ctx.source_text)
    await repo.replace_research_packet(
        session,
        campaign=campaign,
        source_asset_id=ctx.source_asset_id,
        agent_run_id=res.agent_run_id,
        provider=research.RESEARCH_PROVIDER,
        summary=res.value.summary,
        claims=[(c.ordinal, c.claim, c.label, c.source_excerpt) for c in claims],
    )
    await session.commit()
    claims_text = research.claims_block(claims)

    # 2. Strategy.
    strat = await run_agent(
        session,
        strategy.SPEC,
        strategy.variables(
            brand_block=ctx.brand_block,
            campaign_block=ctx.campaign_block,
            research_summary=res.value.summary,
            claims=claims_text,
        ),
        deps=deps,
        scope=_child(scope, res.agent_run_id),
        input_ref=base_ref,
    )
    plan_strategy = strategy.clamp_platforms(
        strat.value, ctx.primary_platform, ctx.secondary_platforms
    )
    await repo.add_strategy(
        session,
        campaign=campaign,
        agent_run_id=strat.agent_run_id,
        strategy=plan_strategy.model_dump(mode="json"),
        positioning=plan_strategy.positioning,
        funnel_stage=plan_strategy.funnel_stage,
        primary_cta=plan_strategy.primary_cta,
        primary_platform=plan_strategy.primary_platform,
        secondary_platforms=plan_strategy.secondary_platforms,
        actor=_STRATEGY_ACTOR,
    )
    await session.commit()
    strategy_text = strategy.strategy_block(plan_strategy)

    # 3. Opportunities.
    opp = await run_agent(
        session,
        opportunity.SPEC,
        opportunity.variables(
            brand_block=ctx.brand_block,
            campaign_block=ctx.campaign_block,
            strategy=strategy_text,
            claims=claims_text,
            open_questions="\n".join(f"- {q}" for q in res.value.open_questions) or "(none)",
        ),
        deps=deps,
        scope=_child(scope, strat.agent_run_id),
        input_ref=base_ref,
    )
    ideas = [opportunity.clean_idea(i, ctx.platforms, len(claims)) for i in opp.value.ideas]
    idea_ids: list[uuid.UUID] = []
    for idea in ideas:
        idea_ids.append(
            await repo.add_idea(
                session,
                campaign=campaign,
                agent_run_id=opp.agent_run_id,
                audience_id=ctx.audience_id,
                values={
                    "angle": idea.angle,
                    "hook": idea.hook,
                    "platform_targets": list(idea.platform_targets),
                    "estimated_value": Decimal(str(round(idea.estimated_value, 3))),
                    "evidence": [
                        {"summary": idea.summary},
                        *({"claim_ordinal": n} for n in idea.evidence_claims),
                    ],
                },
                correlation_id=correlation_id,
            )
        )
    await session.commit()

    # 4. Deterministic mix selection (CI4 for older mixes, then CI2).
    await repo.deselect_previous_ideas(
        session,
        campaign_id=ctx.campaign_id,
        brand_id=ctx.brand_id,
        keep_run_id=opp.agent_run_id,
        correlation_id=correlation_id,
    )
    selected = opportunity.select_mix(ideas, PLAN_MIX_SIZE)
    for ranked in selected:
        await repo.select_idea(
            session,
            idea_id=idea_ids[ranked.index],
            brand_id=ctx.brand_id,
            correlation_id=correlation_id,
        )
    await session.commit()

    # 5. 7-day plan.
    plan = await run_agent(
        session,
        planner.SPEC,
        planner.variables(
            campaign_block=ctx.campaign_block,
            strategy=strategy_text,
            selected_ideas=opportunity.selected_block(selected),
            platforms="; ".join(p.value for p in ctx.platforms),
        ),
        deps=deps,
        scope=_child(scope, opp.agent_run_id),
        input_ref=base_ref,
    )
    entries = planner.schedule(
        plan.value,
        selected_count=len(selected),
        platforms=ctx.platforms,
        start=_start_date(ctx, today),
        timezone=ctx.timezone,
    )
    rows: list[PlanItem] = []
    for scheduled in entries:
        entry = scheduled.entry
        picked = selected[scheduled.idea_index] if scheduled.idea_index is not None else None
        idea_id = idea_ids[picked.index] if picked is not None else None
        # The referenced idea's angle wins over the planner's restatement of it.
        angle = picked.idea.angle if picked is not None else entry.angle
        depends_on = rows[entry.depends_on - 1].id if entry.depends_on else None
        rows.append(
            PlanItem(
                id=uuid7(),
                brand_id=ctx.brand_id,
                campaign_id=ctx.campaign_id,
                agent_run_id=plan.agent_run_id,
                horizon_days=planner.HORIZON_DAYS,
                ordinal=scheduled.ordinal,
                platform=entry.platform,
                format=entry.format,
                angle=angle,
                objective=entry.objective,
                funnel_stage=entry.funnel_stage,
                cta=entry.cta,
                source_asset_id=ctx.source_asset_id,
                content_idea_id=idea_id,
                depends_on_plan_item_id=depends_on,
                window_start=scheduled.window_start,
                window_end=scheduled.window_end,
            )
        )
    await repo.replace_plan(session, ctx.campaign_id, planner.HORIZON_DAYS, rows)
    await repo.transition_campaign(
        session,
        campaign_id=ctx.campaign_id,
        brand_id=ctx.brand_id,
        from_state=CampaignStatus.PLANNING,
        trigger="planning_succeeded",
        actor=_PLANNER_SYSTEM,
        correlation_id=correlation_id,
    )
    await session.commit()

    summary = {
        "campaign_id": str(campaign_id),
        "research_claims": len(claims),
        "claims_downgraded": sum(c.downgraded for c in claims),
        "ideas": len(ideas),
        "selected_ideas": len(selected),
        "plan_items": len(rows),
        "agent_runs": {
            "research": str(res.agent_run_id),
            "strategy": str(strat.agent_run_id),
            "opportunity": str(opp.agent_run_id),
            "planner": str(plan.agent_run_id),
        },
    }
    log.info("campaign planned", extra={"campaign_id": str(campaign_id)})
    return summary


def _child(scope: AgentScope, parent: uuid.UUID) -> AgentScope:
    return dataclasses.replace(scope, parent_agent_run_id=parent)
