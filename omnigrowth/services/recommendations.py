"""Recommendations (§55 step 16, D1): run the Optimization Agent over stored snapshots.

Every persisted recommendation cites >= 1 existing ``analytics_snapshots`` id (DB CHECK
``cardinality >= 1`` plus app validation of each id, OQ-11). Recommendations whose cited
labels do not resolve are dropped and counted, never stored.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.agents import optimizer
from omnigrowth.agents.base import AgentDeps, AgentScope, run_agent
from omnigrowth.campaigns.context import load_planning_context
from omnigrowth.core.enums import ActorType, JobKind
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.logging import get_context
from omnigrowth.core.state_machines import (
    RECOMMENDATION,
    Actor,
    apply_transition,
    create_with_state,
)
from omnigrowth.models import AnalyticsSnapshot, AuditEvent, Campaign, Recommendation
from omnigrowth.schemas.analytics import RecommendationRead
from omnigrowth.services.analytics import campaign_report
from omnigrowth.services.jobs import JobQueue, cancel_queued, create_job, dispatch

log = logging.getLogger(__name__)

_RECOMMENDATIONS = cast(Table, Recommendation.__table__)
_AUDIT = cast(Table, AuditEvent.__table__)


async def request_generation(
    session: AsyncSession, queue: JobQueue, actor: Actor, campaign_id: uuid.UUID
) -> uuid.UUID:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "campaign not found")
    job_id = await create_job(
        session,
        kind=JobKind.RECOMMENDATION_GENERATION,
        actor=actor,
        brand_id=campaign.brand_id,
        subject_type="campaigns",
        subject_id=campaign_id,
    )
    await session.commit()
    try:
        dispatch(queue, JobKind.RECOMMENDATION_GENERATION, job_id)
    except OmniGrowthError:
        await cancel_queued(session, job_id, reason="queue unavailable")
        await session.commit()
        raise
    return job_id


def _labelled_rows(report_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows with a snapshot, in a content-derived order (stable prompt -> stable fixtures)."""
    rows = [r for r in report_rows if r["snapshot_id"] is not None]
    rows.sort(key=lambda r: (r["platform"], r["format"], r["hook"] or "", r["word_count"]))
    labelled: list[dict[str, Any]] = []
    for n, row in enumerate(rows, start=1):
        derived = row["derived"]
        labelled.append(
            {
                **row,
                **row["metrics"],
                "label": f"S{n}",
                "engagement_rate": derived["engagement_rate"]["value"],
                "click_rate": derived["click_rate"]["value"],
                "share_rate": derived["share_rate"]["value"],
            }
        )
    return labelled


async def generate(
    session: AsyncSession, campaign_id: uuid.UUID, job_id: uuid.UUID, *, deps: AgentDeps
) -> dict[str, Any]:
    report = await campaign_report(session, campaign_id)
    rows = _labelled_rows(report.publications)
    if not rows:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "no analytics snapshots to analyse yet",
            details={"failed_guard": "has_snapshots"},
        )
    ctx = await load_planning_context(session, campaign_id)
    outcome = await run_agent(
        session,
        optimizer.SPEC,
        optimizer.variables(campaign=ctx.campaign_block, report=optimizer.report_table(rows)),
        deps=deps,
        scope=AgentScope(
            brand_id=ctx.brand_id,
            campaign_id=campaign_id,
            correlation_id=str(job_id),
            routing_profile=ctx.routing_profile,
            brand_profile_id=ctx.brand_profile_id,
        ),
        input_ref={"campaign_id": str(campaign_id), "snapshots": len(rows)},
    )
    by_label = {r["label"]: uuid.UUID(r["snapshot_id"]) for r in rows}
    by_source: dict[str, str | None] = {r["label"]: r.get("source") for r in rows}
    # Re-check that every id still resolves (arrays cannot carry FKs).
    existing = set(
        (
            await session.execute(
                select(AnalyticsSnapshot.id).where(AnalyticsSnapshot.id.in_(by_label.values()))
            )
        ).scalars()
    )
    stored: list[str] = []
    dropped: list[dict[str, Any]] = []
    for rec in outcome.value.recommendations:
        labels = list(dict.fromkeys(rec.evidence))
        valid = [lbl for lbl in labels if lbl in by_label and by_label[lbl] in existing]
        # Every cited row must exist: a rationale resting partly on invented data is dropped.
        if not valid or len(valid) != len(labels):
            dropped.append({"statement": rec.statement[:120], "evidence": rec.evidence})
            continue
        cited = [by_label[lbl] for lbl in valid]
        rec_id = uuid7()
        await create_with_state(
            session,
            machine=RECOMMENDATION,
            table=_RECOMMENDATIONS,
            audit_table=_AUDIT,
            row={
                "id": rec_id,
                "brand_id": ctx.brand_id,
                "campaign_id": campaign_id,
                "agent_run_id": outcome.agent_run_id,
                "statement": rec.statement,
                "rationale": rec.rationale,
                "target": rec.target,
                "evidence_snapshot_ids": cited,
                "evidence_query": {
                    "source": "campaign_report",
                    "campaign_id": str(campaign_id),
                    "labels": valid,
                    # Simulated (MOCK/FIXTURE) evidence must stay recognisable downstream.
                    "data_sources": sorted(
                        {by_source[lbl] for lbl in valid if by_source.get(lbl) is not None}
                    ),
                    "simulated_evidence": any(
                        by_source.get(lbl) in {"MOCK", "FIXTURE"} for lbl in valid
                    ),
                },
            },
            trigger="create",
            actor=Actor(ActorType.AGENT, optimizer.SPEC.name),
            correlation_id=str(job_id),
            brand_id=ctx.brand_id,
        )
        stored.append(str(rec_id))
    if not stored:
        await session.rollback()
        raise OmniGrowthError(
            ErrorCode.LLM_OUTPUT_INVALID,
            "no recommendation cited an existing analytics snapshot",
            details={"dropped": len(dropped)},
        )
    await session.commit()
    if dropped:
        log.warning("recommendations without evidence dropped", extra={"count": len(dropped)})
    return {
        "recommendations": stored,
        "dropped": dropped,
        "agent_run_id": str(outcome.agent_run_id),
    }


async def decide(
    session: AsyncSession, actor: Actor, recommendation_id: uuid.UUID, trigger: str
) -> RecommendationRead:
    """RC2 ``accept`` / RC3 ``dismiss`` by a human, recorded with who decided."""
    row = await session.get(Recommendation, recommendation_id)
    if row is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "recommendation not found")
    await apply_transition(
        session,
        machine=RECOMMENDATION,
        table=_RECOMMENDATIONS,
        audit_table=_AUDIT,
        entity_id=row.id,
        from_state=row.status,
        trigger=trigger,
        actor=actor,
        correlation_id=get_context().get("correlation_id") or uuid7().hex,
        brand_id=row.brand_id,
        values={"decided_by_actor_type": actor.type, "decided_by_actor_id": actor.id},
    )
    await session.commit()
    refreshed = await session.get(Recommendation, recommendation_id, populate_existing=True)
    return RecommendationRead.model_validate(refreshed)


async def list_recommendations(
    session: AsyncSession, campaign_id: uuid.UUID | None, brand_id: uuid.UUID | None
) -> list[RecommendationRead]:
    stmt = select(Recommendation)
    if campaign_id is not None:
        stmt = stmt.where(Recommendation.campaign_id == campaign_id)
    if brand_id is not None:
        stmt = stmt.where(Recommendation.brand_id == brand_id)
    rows = (await session.execute(stmt.order_by(Recommendation.created_at))).scalars()
    return [RecommendationRead.model_validate(r) for r in rows]
