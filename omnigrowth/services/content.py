"""Content assets (API_PLAN §2.6): generation from the plan, QA, human edits, versions.

Flow (STATE_MACHINES §2): ``generate_assets`` creates DRAFT assets for plan items (CA1) and
one ASSET_GENERATION job; the job runs the platform writer per asset, stores version 1
(GENERATED, with prompt version + model run), moves the asset to GENERATED (CA2) and runs
QA stages 1-4 -> QA_PASSED -> AWAITING_APPROVAL (CA4, CA6) or QA_FAILED (CA5). A human edit
creates a HUMAN_EDIT version (§36), lands in GENERATED (CA16) and re-runs QA as a QA_RUN job.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.agents import writers
from omnigrowth.agents.base import AgentDeps, AgentScope, run_agent
from omnigrowth.agents.strategy import StrategyOutput, strategy_block
from omnigrowth.campaigns.context import PlanningContext, load_planning_context
from omnigrowth.content.diff import diff_versions
from omnigrowth.content.qa import (
    CHECKER,
    ClaimRef,
    QaConfig,
    QaContext,
    SimilarHit,
    overall_score,
    run_qa,
)
from omnigrowth.core.enums import (
    ActorType,
    ApprovalActionType,
    CampaignStatus,
    ContentAssetStatus,
    ContentVersionOrigin,
    EmbeddingField,
    JobKind,
    Platform,
    QaOutcome,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.logging import get_context
from omnigrowth.core.state_machines import CONTENT_ASSET, Actor
from omnigrowth.models import (
    Brand,
    BrandProfile,
    Campaign,
    ContentAsset,
    ContentIdea,
    ContentVersion,
    Offer,
    PlanItem,
)
from omnigrowth.providers.embeddings import embed_texts
from omnigrowth.publishing.scheduler import cancel_open_job
from omnigrowth.schemas.content import (
    ContentAssetRead,
    ContentVersionCreate,
    ContentVersionDiffRead,
    ContentVersionRead,
    FieldChangeRead,
    QaResultRead,
    SimilarContentRead,
)
from omnigrowth.services.jobs import JobQueue, cancel_queued, create_job, dispatch
from omnigrowth.storage import campaigns as campaign_repo
from omnigrowth.storage import content as repo

log = logging.getLogger(__name__)

_QA_ACTOR = Actor(ActorType.SYSTEM, "content-qa")
_AUTO_APPROVER = Actor(ActorType.SYSTEM, "auto-approve")
_SOURCE_EXCERPT_CHARS = 4000
_MUTABLE_CAMPAIGN = (CampaignStatus.ACTIVE, CampaignStatus.PAUSED)


def _correlation() -> str:
    return get_context().get("correlation_id") or uuid7().hex


def _not_found(entity: str) -> OmniGrowthError:
    return OmniGrowthError(ErrorCode.NOT_FOUND, f"{entity} not found")


@dataclass(frozen=True, slots=True)
class ContentDeps:
    agent: AgentDeps
    qa: QaConfig
    # Where a QA re-run goes when QA itself errored after generation (None in tests that
    # drive jobs by hand: the asset is then reported as ``qa_pending``).
    queue: JobQueue | None = None


# ------------------------------------------------------------------ generate (API side)
async def generate_assets(
    session: AsyncSession,
    queue: JobQueue,
    actor: Actor,
    campaign_id: uuid.UUID,
    plan_item_ids: Sequence[uuid.UUID] | None = None,
) -> uuid.UUID:
    """CA1 for every plan item without an asset (writer platforms only) + one job."""
    campaign = await campaign_repo.get_campaign(session, campaign_id, lock=True)
    if campaign is None:
        raise _not_found("campaign")
    if campaign.status is not CampaignStatus.ACTIVE:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION,
            "assets can only be generated for an ACTIVE campaign",
            details={"failed_guard": "campaign_active", "status": campaign.status.value},
        )
    plan = await campaign_repo.list_plan(session, campaign_id, 7)
    if plan_item_ids is not None:
        wanted = set(plan_item_ids)
        unknown = wanted - {p.id for p in plan}
        if unknown:
            raise OmniGrowthError(
                ErrorCode.NOT_FOUND,
                "plan item not found",
                details={"plan_item_ids": sorted(str(u) for u in unknown)},
            )
        plan = [p for p in plan if p.id in wanted]
    existing = await repo.assets_by_plan_item(session, campaign_id)
    job_id = await create_job(
        session,
        kind=JobKind.ASSET_GENERATION,
        actor=actor,
        brand_id=campaign.brand_id,
        subject_type="campaigns",
        subject_id=campaign_id,
    )
    correlation_id = _correlation()
    retry: list[uuid.UUID] = []
    for item in plan:
        if item.platform not in writers.WRITERS:
            continue  # no MVP writer: reported as skipped by the job (D2)
        current = existing.get(item.id)
        if current is not None:
            # A DRAFT asset whose earlier generation failed or never ran is picked up by
            # this request; anything past DRAFT already has content.
            if current.status is ContentAssetStatus.DRAFT:
                retry.append(current.id)
            continue
        await repo.create_asset(
            session,
            row={
                "brand_id": campaign.brand_id,
                "campaign_id": campaign_id,
                "plan_item_id": item.id,
                "content_idea_id": item.content_idea_id,
                "platform": item.platform,
                "format": item.format,
                "generation_request_id": job_id,
                "audience_id": campaign.audience_id,
                "offer_id": campaign.offer_id,
                "pillar": campaign.content_pillar,
                "topic": campaign.topic,
                "angle": item.angle,
                "funnel_stage": item.funnel_stage,
            },
            actor=actor,
            correlation_id=correlation_id,
        )
    await repo.claim_for_generation(session, retry, job_id)
    await session.commit()
    try:
        dispatch(queue, JobKind.ASSET_GENERATION, job_id)
    except OmniGrowthError:
        # Assets stay DRAFT; the next generate-assets request claims them again.
        await cancel_queued(session, job_id, reason="queue unavailable")
        await session.commit()
        raise
    return job_id


# ------------------------------------------------------------------ generate (worker side)
@dataclass(frozen=True, slots=True)
class _WriterContext:
    planning: PlanningContext
    strategy: str
    claims: str
    claim_refs: dict[int, ClaimRef]
    evidence: str
    allowed_urls: frozenset[str]
    banned: tuple[str, ...]
    avoid: tuple[str, ...]


async def _writer_context(session: AsyncSession, campaign: Campaign) -> _WriterContext:
    planning = await load_planning_context(session, campaign.id)
    strategy_row = await campaign_repo.active_strategy(session, campaign.id)
    strategy = (
        strategy_block(StrategyOutput.model_validate(strategy_row.strategy))
        if strategy_row is not None
        else "(none)"
    )
    packet = await campaign_repo.current_research_packet(session, campaign.id)
    claims = packet[1] if packet else []
    claim_refs = {c.ordinal: ClaimRef(c.label, c.claim_text, c.citation_url) for c in claims}
    claims_text = (
        "\n".join(f"[{c.ordinal}] ({c.label.value}) {c.claim_text}" for c in claims) or "(none)"
    )
    evidence = "\n".join(
        [planning.source_text]
        + [c.claim_text for c in claims]
        + [c.source_excerpt for c in claims if c.source_excerpt]
    )
    urls = {c.citation_url for c in claims if c.citation_url}
    if campaign.offer_id is not None:
        offer = await session.get(Offer, campaign.offer_id)
        if offer is not None and offer.funnel_destination_url:
            urls.add(offer.funnel_destination_url)
    profile = (
        await session.execute(
            select(BrandProfile).where(
                BrandProfile.brand_id == campaign.brand_id, BrandProfile.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    return _WriterContext(
        planning=planning,
        strategy=strategy,
        claims=claims_text,
        claim_refs=claim_refs,
        evidence=evidence,
        allowed_urls=frozenset(urls),
        banned=tuple(profile.banned_phrases) if profile else (),
        avoid=tuple(profile.avoid_list) if profile else (),
    )


@dataclass(frozen=True, slots=True)
class _ItemInfo:
    """Plain snapshot of a plan item (ORM rows expire when a failed asset rolls back)."""

    objective: str
    funnel_stage: str
    cta: str | None


def _plan_item_block(item: _ItemInfo | None, asset: ContentAsset) -> str:
    lines = [f"Platform: {asset.platform.value}", f"Format: {asset.format.value}"]
    if asset.angle:
        lines.append(f"Angle: {asset.angle.value}")
    if item is not None:
        lines += [
            f"Objective: {item.objective}",
            f"Funnel stage: {item.funnel_stage}",
            f"Call to action: {item.cta or '(none)'}",
        ]
    return "\n".join(lines)


async def _idea_block(session: AsyncSession, asset: ContentAsset) -> str:
    if asset.content_idea_id is None:
        return asset.topic or "(none)"
    idea = await session.get(ContentIdea, asset.content_idea_id)
    if idea is None:
        return asset.topic or "(none)"
    summaries = [e["summary"] for e in idea.evidence if isinstance(e, dict) and "summary" in e]
    return "\n".join([f"Hook idea: {idea.hook}", *summaries])


async def generate_job(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    deps: ContentDeps,
) -> dict[str, Any]:
    """Write + QA every DRAFT asset created by this request. Per-asset model failures leave
    that asset in DRAFT (STATE_MACHINES §2) and are reported; the job fails only when
    nothing could be generated."""
    campaign = await campaign_repo.get_campaign(session, campaign_id)
    if campaign is None:
        raise _not_found("campaign")
    correlation_id = str(job_id)
    plan = await campaign_repo.list_plan(session, campaign_id, 7)
    plan_by_id = {p.id: _ItemInfo(p.objective.value, p.funnel_stage.value, p.cta) for p in plan}
    skipped = [
        {"plan_item_id": str(p.id), "platform": p.platform.value, "reason": "NOT_IMPLEMENTED"}
        for p in plan
        if p.platform not in writers.WRITERS
    ]
    assets = await repo.list_assets(
        session,
        campaign_id=campaign_id,
        statuses=[ContentAssetStatus.DRAFT],
        generation_request_id=job_id,
    )
    if not assets:
        return {"generated": [], "failed": [], "skipped": skipped}
    ctx = await _writer_context(session, campaign)
    generated: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    qa_pending: list[dict[str, Any]] = []
    last_error: OmniGrowthError | None = None
    # Plan order, not created_at: assets of one request share a timestamp, and the order
    # must be deterministic (fixture replay, A8).
    ordinal = {p.id: p.ordinal for p in plan}
    ordered = sorted(
        assets,
        key=lambda a: (ordinal.get(a.plan_item_id, 1_000_000) if a.plan_item_id else 0, str(a.id)),
    )
    for asset_id in [a.id for a in ordered]:
        asset = await repo.get_asset(session, asset_id)
        if (
            asset is None
            or asset.status is not ContentAssetStatus.DRAFT
            or asset.generation_request_id != job_id
        ):
            continue  # superseded or already generated by an earlier attempt
        item = plan_by_id.get(asset.plan_item_id) if asset.plan_item_id else None
        try:
            regenerating = asset.current_version_id is not None
            status = await _generate_one(
                session,
                asset,
                item,
                ctx,
                deps=deps,
                correlation_id=correlation_id,
                origin=(
                    ContentVersionOrigin.REGENERATION
                    if regenerating
                    else ContentVersionOrigin.GENERATED
                ),
                revision_notes=await _revision_notes(session, asset) if regenerating else "(none)",
            )
        except OmniGrowthError as exc:
            await session.rollback()
            reloaded = await repo.get_asset(session, asset_id)
            if reloaded is not None and reloaded.status is ContentAssetStatus.GENERATED:
                # The content exists (CA2 committed); only QA failed to run. Never leave it
                # silently without QA: queue a re-run, or report it for POST /content/{id}/qa.
                entry = {"content_asset_id": str(asset_id), "error_code": exc.code.value}
                if deps.queue is not None:
                    entry["qa_job_id"] = str(
                        await request_qa(session, deps.queue, _QA_ACTOR, asset_id)
                    )
                qa_pending.append(entry)
                generated.append({"content_asset_id": str(asset_id), "status": "GENERATED"})
                continue
            log.warning(
                "asset generation failed",
                extra={"content_asset_id": str(asset_id), "error_code": str(exc.code)},
            )
            failed.append({"content_asset_id": str(asset_id), "error_code": exc.code.value})
            last_error = exc
            continue
        generated.append({"content_asset_id": str(asset_id), "status": status})
    if not generated and last_error is not None:
        raise last_error
    return {
        "generated": generated,
        "failed": failed,
        "skipped": skipped,
        "qa_pending": qa_pending,
    }


async def _generate_one(
    session: AsyncSession,
    asset: ContentAsset,
    item: _ItemInfo | None,
    ctx: _WriterContext,
    *,
    deps: ContentDeps,
    correlation_id: str,
    origin: ContentVersionOrigin = ContentVersionOrigin.GENERATED,
    revision_notes: str = "(none)",
) -> str:
    spec = writers.WRITERS[asset.platform]
    generation_request_id = asset.generation_request_id
    outcome = await run_agent(
        session,
        spec,
        writers.variables(
            brand=ctx.planning.brand_block,
            strategy=ctx.strategy,
            plan_item=_plan_item_block(item, asset),
            idea=await _idea_block(session, asset),
            claims=ctx.claims,
            source=ctx.planning.source_text[:_SOURCE_EXCERPT_CHARS],
            revision_notes=revision_notes,
        ),
        deps=deps.agent,
        scope=AgentScope(
            brand_id=asset.brand_id,
            campaign_id=asset.campaign_id,
            correlation_id=correlation_id,
            routing_profile=ctx.planning.routing_profile,
            brand_profile_id=ctx.planning.brand_profile_id,
        ),
        input_ref={"content_asset_id": str(asset.id), "plan_item_id": str(asset.plan_item_id)},
    )
    fields = writers.fields_from_output(outcome.value)
    version_id = await repo.add_version(
        session,
        asset.id,
        repo.NewVersion(
            origin=origin,
            # A regeneration records what it replaces (§36 history); a first draft has none.
            based_on_version_id=(
                asset.current_version_id if origin is ContentVersionOrigin.REGENERATION else None
            ),
            hook=fields.hook,
            body=fields.body,
            cta=fields.cta,
            platform_payload=fields.payload,
            media_requirements=list(fields.media_requirements),
            author=Actor(ActorType.AGENT, spec.name),
            hook_type=fields.hook_type,
            cta_type=fields.cta_type,
            prompt_version_id=outcome.prompt_version_id,
            model_run_id=outcome.model_run_id,
            agent_run_id=outcome.agent_run_id,
            brand_profile_id=ctx.planning.brand_profile_id,
        ),
    )
    # CA2 only for the request this asset is waiting for (stale results are dropped).
    await repo.transition_asset(
        session,
        asset,
        from_state=ContentAssetStatus.DRAFT,
        trigger="generated",
        actor=Actor(ActorType.AGENT, spec.name),
        correlation_id=correlation_id,
        values={"current_version_id": version_id},
        fence={"generation_request_id": generation_request_id},
    )
    await session.commit()
    return await run_asset_qa(
        session, asset.id, deps=deps, correlation_id=correlation_id, writer_ctx=ctx
    )


async def _revision_notes(session: AsyncSession, asset: ContentAsset) -> str:
    """Instructions of the latest REGENERATE request for this asset (§37)."""
    history = await repo.approval_history(session, asset.id)
    for action in reversed(history):
        if action.action is ApprovalActionType.REGENERATE:
            return action.comment or "(none)"
    return "(none)"


async def regenerate_asset(
    session: AsyncSession,
    queue: JobQueue,
    actor: Actor,
    asset_id: uuid.UUID,
    content_version_id: uuid.UUID,
    instructions: str | None,
) -> uuid.UUID:
    """§37 REGENERATE: CA18 (or CA19 after cancelling the pending job) -> DRAFT, then the
    writer produces a REGENERATION version from the instructions (ASSET_GENERATION job)."""
    asset = await repo.get_asset(session, asset_id, lock=True)
    if asset is None:
        raise _not_found("content asset")
    campaign = await campaign_repo.get_campaign(session, asset.campaign_id)
    if campaign is None or campaign.status not in _MUTABLE_CAMPAIGN:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION,
            "content of this campaign is frozen",
            details={"failed_guard": "campaign_mutable"},
        )
    CONTENT_ASSET.resolve(asset.status, "REGENERATE", actor)
    if asset.current_version_id != content_version_id:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "the asset changed since this version was reviewed",
            details={"current_version_id": str(asset.current_version_id)},
        )
    correlation_id = _correlation()
    from_status = asset.status.value
    if asset.status is ContentAssetStatus.SCHEDULED:
        await cancel_open_job(session, asset.id, actor, reason="regenerate")
    job_id = await create_job(
        session,
        kind=JobKind.ASSET_GENERATION,
        actor=actor,
        brand_id=asset.brand_id,
        subject_type="campaigns",
        subject_id=asset.campaign_id,
    )
    target = await repo.transition_asset(
        session,
        asset,
        trigger="REGENERATE",
        actor=actor,
        correlation_id=correlation_id,
        values={"approved_version_id": None, "generation_request_id": job_id},
        extra={"invalidated_approval_of": str(asset.approved_version_id)}
        if asset.approved_version_id
        else None,
    )
    await repo.add_approval_action(
        session,
        asset=asset,
        version_id=content_version_id,
        resulting_version_id=None,
        action=ApprovalActionType.REGENERATE,
        from_status=from_status,
        to_status=target,
        actor=actor,
        correlation_id=correlation_id,
        comment=instructions,
    )
    await session.commit()
    try:
        dispatch(queue, JobKind.ASSET_GENERATION, job_id)
    except OmniGrowthError:
        await cancel_queued(session, job_id, reason="queue unavailable")
        await session.commit()
        raise
    return job_id


# ------------------------------------------------------------------ QA
async def run_asset_qa(
    session: AsyncSession,
    asset_id: uuid.UUID,
    *,
    deps: ContentDeps,
    correlation_id: str,
    writer_ctx: _WriterContext | None = None,
) -> str:
    """QA stages 1-4 on the current version of a GENERATED asset; returns the new status.

    The embedding call happens *before* the asset row is locked (a slow model must not block
    edits); the lock is then taken and the asset re-checked, and every transition is fenced
    on the version QA actually checked.
    """
    asset = await repo.get_asset(session, asset_id)
    if asset is None:
        raise _not_found("content asset")
    if asset.status is not ContentAssetStatus.GENERATED or asset.current_version_id is None:
        return asset.status.value  # QA already applied or asset moved on (idempotent)
    version = await repo.get_version(session, asset.id, asset.current_version_id)
    if version is None:
        raise _not_found("content version")
    campaign = await campaign_repo.get_campaign(session, asset.campaign_id)
    if campaign is None:
        raise _not_found("campaign")
    ctx = writer_ctx or await _writer_context(session, campaign)
    item = await session.get(PlanItem, asset.plan_item_id) if asset.plan_item_id else None
    draft = writers.build_draft(asset.platform, asset.format, _fields(version))
    similar, available, embedding_model = await _similarity(
        session, asset, version, draft.segments, deps
    )
    await session.commit()  # keep the stored vectors; nothing is locked during the call above
    locked = await repo.get_asset(session, asset_id, lock=True)
    if (
        locked is None
        or locked.status is not ContentAssetStatus.GENERATED
        or locked.current_version_id != version.id
    ):
        return locked.status.value if locked else "MISSING"  # moved on meanwhile
    asset = locked
    qa_ctx = QaContext(
        cta_required=bool(item and item.cta),
        expected_cta=item.cta if item else None,
        banned_phrases=ctx.banned,
        avoid_terms=ctx.avoid,
        claims=ctx.claim_refs,
        evidence_text=ctx.evidence,
        allowed_urls=ctx.allowed_urls,
        similar=similar,
        similarity_checked=available,
        embedding_model=embedding_model,
    )
    results = run_qa(draft, qa_ctx, deps.qa)
    await repo.save_qa_results(
        session,
        version.id,
        [
            {
                "stage": r.stage,
                "outcome": r.outcome,
                "score": Decimal(r.score),
                "findings": [f.as_json() for f in r.findings],
                "checker": CHECKER,
            }
            for r in results
        ],
    )
    failed = any(r.outcome is QaOutcome.FAIL for r in results)
    fence = {"current_version_id": version.id}
    values = {"quality_score": Decimal(overall_score(results))}
    if failed:
        target = await repo.transition_asset(
            session,
            asset,
            trigger="qa_failed",
            actor=_QA_ACTOR,
            correlation_id=correlation_id,
            values=values,
            fence=fence,
        )
    else:
        await repo.transition_asset(
            session,
            asset,
            trigger="qa_passed",
            actor=_QA_ACTOR,
            correlation_id=correlation_id,
            values=values,
            fence=fence,
        )
        # CA6 is immediate.
        target = await repo.transition_asset(
            session,
            asset,
            from_state=ContentAssetStatus.QA_PASSED,
            trigger="submit_for_approval",
            actor=_QA_ACTOR,
            correlation_id=correlation_id,
            fence=fence,
        )
        brand = await session.get(Brand, asset.brand_id)
        if brand is not None and brand.auto_approve_enabled:
            # CA7 by SYSTEM (STATE_MACHINES §2, guard auto_approve_allowed): only content that
            # passed all four QA stages, and never sufficient on its own for LIVE (A6).
            target = await repo.transition_asset(
                session,
                asset,
                from_state=ContentAssetStatus.AWAITING_APPROVAL,
                trigger="APPROVE",
                actor=_AUTO_APPROVER,
                correlation_id=correlation_id,
                values={"approved_version_id": version.id},
                fence=fence,
                reason="brand auto-approve",
            )
            await repo.add_approval_action(
                session,
                asset=asset,
                version_id=version.id,
                resulting_version_id=None,
                action=ApprovalActionType.APPROVE,
                from_status=ContentAssetStatus.AWAITING_APPROVAL.value,
                to_status=target,
                actor=_AUTO_APPROVER,
                correlation_id=correlation_id,
                comment="auto-approved (brand setting)",
            )
    await session.commit()
    return target


def _fields(version: ContentVersion) -> writers.VersionFields:
    payload = version.platform_payload or {}
    return writers.VersionFields(
        hook=version.hook or version.body.split("\n", 1)[0],
        body=version.body,
        cta=version.cta,
        hashtags=[str(t) for t in payload.get("hashtags", [])],
        cited_claims=[int(n) for n in payload.get("cited_claims", [])],
    )


async def _similarity(
    session: AsyncSession,
    asset: ContentAsset,
    version: ContentVersion,
    segments: tuple[str, ...],
    deps: ContentDeps,
) -> tuple[tuple[SimilarHit, ...], bool, str | None]:
    """Embed FULL + HOOK, find neighbours among the brand's other assets, store vectors.

    Duplicate detection is warn-only (§10): if no embedding model is reachable QA continues
    and reports that the check was skipped instead of failing the content.
    """
    full_text = "\n\n".join(segments)
    hook = version.hook or full_text.split("\n", 1)[0]
    try:
        embedded = await embed_texts(deps.agent.router, [full_text, hook])
    except OmniGrowthError as exc:
        log.warning(
            "duplicate check skipped: embeddings unavailable",
            extra={"content_asset_id": str(asset.id), "error_code": str(exc.code)},
        )
        return (), False, None
    rules = deps.qa.similarity
    hits: list[SimilarHit] = []
    for field, vector, text in (
        (EmbeddingField.FULL, embedded.vectors[0], full_text),
        (EmbeddingField.HOOK, embedded.vectors[1], hook),
    ):
        for n in await repo.nearest_versions(
            session,
            brand_id=asset.brand_id,
            model=embedded.model,
            vector=vector,
            field=field,
            exclude_asset_id=asset.id,
            limit=rules.max_neighbours,
        ):
            hits.append(
                SimilarHit(
                    field.value, str(n.content_asset_id), str(n.content_version_id), n.similarity
                )
            )
        await repo.save_embedding(
            session,
            brand_id=asset.brand_id,
            version_id=version.id,
            field=field,
            model=embedded.model,
            vector=vector,
            text=text,
        )
    return tuple(hits), True, embedded.model


async def request_qa(
    session: AsyncSession,
    queue: JobQueue,
    actor: Actor,
    asset_id: uuid.UUID,
) -> uuid.UUID:
    """QA_RUN job for a GENERATED asset (QA results are only ever for the current version)."""
    asset = await repo.get_asset(session, asset_id)
    if asset is None:
        raise _not_found("content asset")
    if asset.status is not ContentAssetStatus.GENERATED:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION,
            "QA runs on GENERATED content; edit or regenerate to re-check other states",
            details={"status": asset.status.value},
        )
    job_id = await create_job(
        session,
        kind=JobKind.QA_RUN,
        actor=actor,
        brand_id=asset.brand_id,
        subject_type="content_assets",
        subject_id=asset.id,
    )
    await session.commit()
    try:
        dispatch(queue, JobKind.QA_RUN, job_id)
    except OmniGrowthError:
        await cancel_queued(session, job_id, reason="queue unavailable")
        await session.commit()
        raise
    return job_id


# ------------------------------------------------------------------ human edit (§36, §37)
async def edit_asset(
    session: AsyncSession,
    queue: JobQueue,
    actor: Actor,
    asset_id: uuid.UUID,
    body: ContentVersionCreate,
) -> tuple[ContentVersionRead, uuid.UUID]:
    """New HUMAN_EDIT version -> GENERATED (CA16) and a QA_RUN job."""
    asset = await repo.get_asset(session, asset_id, lock=True)
    if asset is None:
        raise _not_found("content asset")
    campaign = await campaign_repo.get_campaign(session, asset.campaign_id)
    if campaign is None or campaign.status not in _MUTABLE_CAMPAIGN:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION,
            "content of this campaign is frozen",
            details={"failed_guard": "campaign_mutable"},
        )
    if asset.status is ContentAssetStatus.DRAFT:
        raise OmniGrowthError(
            ErrorCode.CONFLICT, "generation is in flight for this asset", details={}
        )
    CONTENT_ASSET.resolve(asset.status, "EDIT", actor)
    if asset.current_version_id != body.based_on_version_id:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "the asset changed since this edit started",
            details={"current_version_id": str(asset.current_version_id)},
        )
    base = await repo.get_version(session, asset.id, body.based_on_version_id)
    if base is None:
        raise _not_found("content version")
    correlation_id = _correlation()
    from_status = asset.status.value
    if asset.status is ContentAssetStatus.SCHEDULED:
        # CA17: the pending publication job is cancelled first (J9, X7); refused while the
        # dispatcher holds it or its outcome is unknown.
        await cancel_open_job(session, asset.id, actor, reason="edited")
    base_payload = base.platform_payload or {}
    hashtags = (
        writers.normalize_hashtags(body.hashtags)
        if body.hashtags is not None
        else list(base_payload.get("hashtags", []))
    )
    version_id = await repo.add_version(
        session,
        asset.id,
        repo.NewVersion(
            origin=ContentVersionOrigin.HUMAN_EDIT,
            based_on_version_id=base.id,
            title=body.title if body.title is not None else base.title,
            hook=_edited_hook(asset, body, base),
            body=body.body,
            cta=body.cta if body.cta is not None else base.cta,
            platform_payload={
                "hashtags": hashtags,
                "cited_claims": list(base_payload.get("cited_claims", [])),
            },
            media_requirements=list(base.media_requirements),
            author=actor,
            hook_type=base.hook_type,
            cta_type=base.cta_type,
            brand_profile_id=base.brand_profile_id,
        ),
    )
    target = await repo.transition_asset(
        session,
        asset,
        trigger="EDIT",
        actor=actor,
        correlation_id=correlation_id,
        values={"current_version_id": version_id, "approved_version_id": None},
        # STATE_MACHINES §2.1: an edit never keeps an approval; say which one it voided.
        extra={"invalidated_approval_of": str(asset.approved_version_id)}
        if asset.approved_version_id
        else None,
    )
    await repo.add_approval_action(
        session,
        asset=asset,
        version_id=base.id,
        resulting_version_id=version_id,
        action=ApprovalActionType.EDIT,
        from_status=from_status,
        to_status=target,
        actor=actor,
        correlation_id=correlation_id,
        comment=body.comment,
    )
    job_id = await create_job(
        session,
        kind=JobKind.QA_RUN,
        actor=actor,
        brand_id=asset.brand_id,
        subject_type="content_assets",
        subject_id=asset.id,
    )
    await session.commit()
    try:
        dispatch(queue, JobKind.QA_RUN, job_id)
    except OmniGrowthError:
        # The edit is saved; QA can be re-run once the queue is back.
        await cancel_queued(session, job_id, reason="queue unavailable")
        await session.commit()
        raise
    version = await repo.get_version(session, asset.id, version_id)
    assert version is not None  # noqa: S101 - inserted above in this transaction
    return ContentVersionRead.model_validate(version), job_id


def _edited_hook(
    asset: ContentAsset, body: ContentVersionCreate, base: ContentVersion
) -> str | None:
    if body.hook is not None:
        return body.hook
    if asset.platform is Platform.X:
        # An X hook is the first post: follow the edited body, never a stale copy.
        posts = writers.split_posts(body.body)
        return posts[0].split("\n", 1)[0].strip() if posts else None
    return base.hook


# ------------------------------------------------------------------ reads
async def _require_asset(session: AsyncSession, asset_id: uuid.UUID) -> ContentAsset:
    asset = await repo.get_asset(session, asset_id)
    if asset is None:
        raise _not_found("content asset")
    return asset


async def _asset_read(session: AsyncSession, asset: ContentAsset) -> ContentAssetRead:
    version = (
        await repo.get_version(session, asset.id, asset.current_version_id)
        if asset.current_version_id
        else None
    )
    qa = await repo.qa_results(session, version.id) if version else []
    return ContentAssetRead.model_validate(
        {
            **{c: getattr(asset, c) for c in ContentAssetRead.asset_columns()},
            "current_version": ContentVersionRead.model_validate(version) if version else None,
            "qa": [QaResultRead.model_validate(r) for r in qa],
        }
    )


async def get_asset(session: AsyncSession, asset_id: uuid.UUID) -> ContentAssetRead:
    return await _asset_read(session, await _require_asset(session, asset_id))


async def list_assets(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID | None,
    campaign_id: uuid.UUID | None,
    statuses: Sequence[ContentAssetStatus],
) -> list[ContentAssetRead]:
    rows = await repo.list_assets(
        session, brand_id=brand_id, campaign_id=campaign_id, statuses=statuses
    )
    return [await _asset_read(session, a) for a in rows]


async def list_versions(session: AsyncSession, asset_id: uuid.UUID) -> list[ContentVersionRead]:
    await _require_asset(session, asset_id)
    versions = await repo.list_versions(session, asset_id)
    return [ContentVersionRead.model_validate(v) for v in versions]


async def get_version(
    session: AsyncSession, asset_id: uuid.UUID, version_id: uuid.UUID
) -> ContentVersionRead:
    version = await repo.get_version(session, asset_id, version_id)
    if version is None:
        raise _not_found("content version")
    return ContentVersionRead.model_validate(version)


async def diff(
    session: AsyncSession,
    asset_id: uuid.UUID,
    from_version_id: uuid.UUID | None,
    to_version_id: uuid.UUID | None,
) -> ContentVersionDiffRead:
    """Default: previous version vs current (§36)."""
    asset = await _require_asset(session, asset_id)
    versions = await repo.list_versions(session, asset_id)
    by_id = {v.id: v for v in versions}
    target = by_id.get(to_version_id or asset.current_version_id or uuid.UUID(int=0))
    if target is None:
        raise _not_found("content version")
    if from_version_id is not None:
        source = by_id.get(from_version_id)
    else:
        earlier = [v for v in versions if v.version_no < target.version_no]
        source = earlier[-1] if earlier else None
    if source is None:
        raise _not_found("content version")
    result = diff_versions(source, target)
    return ContentVersionDiffRead(
        from_version_id=source.id,
        to_version_id=target.id,
        from_version_no=source.version_no,
        to_version_no=target.version_no,
        changes=[
            FieldChangeRead(field=c.field, before=c.before, after=c.after) for c in result.changes
        ],
        unified=result.unified,
    )


async def qa_results(
    session: AsyncSession, asset_id: uuid.UUID, version_id: uuid.UUID | None
) -> list[QaResultRead]:
    asset = await _require_asset(session, asset_id)
    wanted = version_id or asset.current_version_id
    if wanted is None or await repo.get_version(session, asset_id, wanted) is None:
        raise _not_found("content version")
    return [QaResultRead.model_validate(r) for r in await repo.qa_results(session, wanted)]


async def similar(
    session: AsyncSession,
    asset_id: uuid.UUID,
    threshold: float | None,
    config: QaConfig,
    limit: int = 10,
) -> list[SimilarContentRead]:
    """§10: stored-vector neighbours of the current version (warn-only information)."""
    asset = await _require_asset(session, asset_id)
    if asset.current_version_id is None:
        return []
    stored = await repo.version_vector(session, asset.current_version_id, EmbeddingField.FULL)
    if stored is None:
        return []
    model, vector = stored
    if threshold is None:
        threshold = config.similarity.for_model(model).near_duplicate
    neighbours = await repo.nearest_versions(
        session,
        brand_id=asset.brand_id,
        model=model,
        vector=vector,
        field=EmbeddingField.FULL,
        exclude_asset_id=asset.id,
        limit=limit,
    )
    return [
        SimilarContentRead(
            content_asset_id=n.content_asset_id,
            content_version_id=n.content_version_id,
            similarity=round(n.similarity, 4),
            near_duplicate=n.similarity >= threshold,
        )
        for n in neighbours
        if n.similarity >= threshold
    ]
