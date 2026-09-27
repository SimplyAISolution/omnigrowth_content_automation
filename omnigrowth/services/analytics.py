"""Analytics (spec §16-17, §34): mock pulls, fixture ingest, campaign report.

Snapshots are immutable and NULL means "not captured" (never 0). The report joins each
publication's latest snapshot to the content attributes it was produced with (§16), so
the Optimization Agent can compare outcomes against platform, format, angle, hook type...
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from omnigrowth.analytics.formula import (
    FORMULA_METRICS,
    SNAPSHOT_METRICS,
    evaluate,
    evaluate_aggregate,
)
from omnigrowth.analytics.metrics import COUNT_METRICS, sum_counts
from omnigrowth.core.enums import (
    ActorType,
    JobKind,
    JobStatus,
    MetricSource,
    Platform,
    PublicationStatus,
    PublishMode,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.state_machines import Actor
from omnigrowth.models import (
    AnalyticsSnapshot,
    Brand,
    Campaign,
    ContentAsset,
    ContentVersion,
    Job,
    ModelRun,
    PromptVersion,
    Publication,
)
from omnigrowth.publishing.config import AnalyticsPullConfig
from omnigrowth.publishing.mock import MockPublisher
from omnigrowth.schemas.analytics import (
    CORRELATION_NOTE,
    SIMULATED_SOURCES,
    AnalyticsSnapshotBatchCreate,
    AnalyticsSnapshotIngestResultRead,
    AnalyticsSnapshotRead,
    AttributeComparisonRead,
    AttributeGroupRead,
    CampaignReportRead,
    DataProvenance,
    DerivedValueRead,
    PublicationMetricsRead,
)
from omnigrowth.services import metric_definitions
from omnigrowth.services.jobs import JobQueue, cancel_queued, create_job, dispatch


def _not_found(entity: str) -> OmniGrowthError:
    return OmniGrowthError(ErrorCode.NOT_FOUND, f"{entity} not found")


async def _require_campaign(session: AsyncSession, campaign_id: uuid.UUID) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise _not_found("campaign")
    return campaign


# ------------------------------------------------------------------ pull (mock, §34)
async def request_pull(
    session: AsyncSession, queue: JobQueue, actor: Actor, campaign_id: uuid.UUID
) -> uuid.UUID:
    campaign = await _require_campaign(session, campaign_id)
    job_id = await create_job(
        session,
        kind=JobKind.ANALYTICS_PULL,
        actor=actor,
        brand_id=campaign.brand_id,
        subject_type="campaigns",
        subject_id=campaign_id,
    )
    await session.commit()
    try:
        dispatch(queue, JobKind.ANALYTICS_PULL, job_id)
    except OmniGrowthError:
        await cancel_queued(session, job_id, reason="queue unavailable")
        await session.commit()
        raise
    return job_id


async def pull_campaign_metrics(
    session: AsyncSession, campaign_id: uuid.UUID, *, now: datetime | None = None
) -> dict[str, Any]:
    """One snapshot per PUBLISHED mock publication (worker side). Real platform metric
    calls arrive with the Phase 13 adapters; until then only MOCK publications are pulled."""
    await _require_campaign(session, campaign_id)
    captured_at = now or datetime.now(UTC)
    rows = (
        await session.execute(
            select(Publication, ContentVersion.body_hash)
            .join(ContentAsset, ContentAsset.id == Publication.content_asset_id)
            .join(ContentVersion, ContentVersion.id == Publication.content_version_id)
            .where(
                ContentAsset.campaign_id == campaign_id,
                Publication.status == PublicationStatus.PUBLISHED,
            )
            .order_by(Publication.published_at, Publication.id)
        )
    ).all()
    publisher = MockPublisher()
    created: list[str] = []
    skipped: list[dict[str, str]] = []
    for pub, body_hash in rows:
        if pub.publish_mode is not PublishMode.MOCK or pub.external_post_id is None:
            skipped.append({"publication_id": str(pub.id), "reason": "NOT_IMPLEMENTED"})
            continue
        metrics = await publisher.fetch_metrics(
            pub.platform, pub.external_post_id, seed=f"{body_hash}:{pub.platform.value}"
        )
        snapshot_id = uuid7()
        inserted = await session.execute(
            pg_insert(AnalyticsSnapshot)
            .values(
                id=snapshot_id,
                brand_id=pub.brand_id,
                publication_id=pub.id,
                captured_at=captured_at,
                source=MetricSource.MOCK,
                raw_payload=metrics.raw,
                impressions=metrics.values.get("impressions"),
                views=metrics.values.get("views"),
                reach=metrics.values.get("reach"),
                likes=metrics.values.get("likes"),
                comments=metrics.values.get("comments"),
                shares=metrics.values.get("shares"),
                saves=metrics.values.get("saves"),
                clicks=metrics.values.get("clicks"),
                profile_visits=metrics.values.get("profile_visits"),
                followers_gained=metrics.values.get("followers_gained"),
                conversion_count=metrics.values.get("conversion_count"),
            )
            .on_conflict_do_nothing(index_elements=["publication_id", "captured_at"])
            .returning(AnalyticsSnapshot.id)
        )
        if inserted.scalar_one_or_none() is not None:
            created.append(str(snapshot_id))
    await session.commit()
    return {"snapshots": created, "skipped": skipped, "captured_at": captured_at.isoformat()}


async def schedule_due_pulls(
    session: AsyncSession,
    queue: JobQueue,
    *,
    now: datetime,
    config: AnalyticsPullConfig,
) -> list[uuid.UUID]:
    """Scheduler side: enqueue one ANALYTICS_PULL per campaign that has a post published in
    the last ``window_days`` and no pull queued, running, or created within
    ``interval_minutes``. Idempotent per tick; never pulls DELETED posts (X9)."""
    if not config.enabled:
        return []
    recent_pull = (
        select(Job.id)
        .where(
            Job.kind == JobKind.ANALYTICS_PULL,
            Job.subject_id == ContentAsset.campaign_id,
            or_(
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                Job.queued_at > now - timedelta(minutes=config.interval_minutes),
            ),
        )
        .exists()
    )
    due = (
        await session.execute(
            select(ContentAsset.campaign_id, ContentAsset.brand_id)
            .join(Publication, Publication.content_asset_id == ContentAsset.id)
            .where(
                Publication.status == PublicationStatus.PUBLISHED,
                Publication.published_at > now - timedelta(days=config.window_days),
                ~recent_pull,
            )
            .group_by(ContentAsset.campaign_id, ContentAsset.brand_id)
            .order_by(ContentAsset.campaign_id)
            .limit(config.max_campaigns_per_tick)
        )
    ).all()
    created: list[uuid.UUID] = []
    for campaign_id, brand_id in due:
        job_id = await create_job(
            session,
            kind=JobKind.ANALYTICS_PULL,
            actor=SCHEDULER_ANALYTICS,
            brand_id=brand_id,
            subject_type="campaigns",
            subject_id=campaign_id,
        )
        await session.commit()
        try:
            dispatch(queue, JobKind.ANALYTICS_PULL, job_id)
        except OmniGrowthError:
            await cancel_queued(session, job_id, reason="queue unavailable")
            await session.commit()
            raise
        created.append(job_id)
    return created


# ------------------------------------------------------------------ fixture ingest
async def ingest_snapshots(
    session: AsyncSession, actor: Actor, body: AnalyticsSnapshotBatchCreate
) -> AnalyticsSnapshotIngestResultRead:
    created: list[uuid.UUID] = []
    duplicates = 0
    for item in body.items:
        pub = await session.get(Publication, item.publication_id)
        if pub is None:
            raise _not_found("publication")
        if item.source is MetricSource.MANUAL and pub.publish_mode is PublishMode.MOCK:
            # A mock post never reached an audience: numbers for it are simulated by
            # definition and must not be labelled as real (A14, docs/METRICS.md §2).
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE,
                "MANUAL metrics need a post that actually went out; use FIXTURE for a "
                "mock publication",
                details={"field": "source", "publication_id": str(pub.id)},
            )
        snapshot_id = uuid7()
        inserted = await session.execute(
            pg_insert(AnalyticsSnapshot)
            .values(
                id=snapshot_id,
                brand_id=pub.brand_id,
                publication_id=pub.id,
                captured_at=item.captured_at,
                source=item.source,
                raw_payload={"ingested_by": f"{actor.type.value}:{actor.id}"},
                impressions=item.impressions,
                views=item.views,
                reach=item.reach,
                likes=item.likes,
                comments=item.comments,
                shares=item.shares,
                saves=item.saves,
                clicks=item.clicks,
                profile_visits=item.profile_visits,
                followers_gained=item.followers_gained,
                conversion_count=item.conversion_count,
            )
            .on_conflict_do_nothing(index_elements=["publication_id", "captured_at"])
            .returning(AnalyticsSnapshot.id)
        )
        if inserted.scalar_one_or_none() is None:
            duplicates += 1
        else:
            created.append(snapshot_id)
    await session.commit()
    return AnalyticsSnapshotIngestResultRead(created=created, duplicates=duplicates)


async def get_snapshot(session: AsyncSession, snapshot_id: uuid.UUID) -> AnalyticsSnapshotRead:
    snapshot = await session.get(AnalyticsSnapshot, snapshot_id)
    if snapshot is None:
        raise _not_found("analytics snapshot")
    return AnalyticsSnapshotRead.model_validate(snapshot)


async def list_snapshots(
    session: AsyncSession, campaign_id: uuid.UUID
) -> list[AnalyticsSnapshotRead]:
    rows = (
        await session.execute(
            select(AnalyticsSnapshot)
            .join(Publication, Publication.id == AnalyticsSnapshot.publication_id)
            .join(ContentAsset, ContentAsset.id == Publication.content_asset_id)
            .where(ContentAsset.campaign_id == campaign_id)
            .order_by(AnalyticsSnapshot.captured_at, AnalyticsSnapshot.id)
        )
    ).scalars()
    return [AnalyticsSnapshotRead.model_validate(s) for s in rows]


# ------------------------------------------------------------------ shared loader (§16)
WORD_COUNT_BUCKETS = ((50, "<50"), (100, "50-99"), (200, "100-199"), (400, "200-399"))
ATTRIBUTES = (
    "topic",
    "pillar",
    "hook_type",
    "platform",
    "format",
    "word_count_bucket",
    "cta_type",
    "media_type",
    "posting_hour",
    "campaign",
    "audience",
    "model",
    "prompt_version",
)
DEFAULT_MIN_SAMPLE = 5
MAX_COMPARISON_ROWS = 5_000


def word_count_bucket(words: int) -> str:
    for limit, label in WORD_COUNT_BUCKETS:
        if words < limit:
            return label
    return "400+"


@dataclass(frozen=True, slots=True)
class PublicationRow:
    publication: Publication
    asset: ContentAsset
    version: ContentVersion
    campaign: Campaign
    snapshot: AnalyticsSnapshot | None
    model: str | None
    prompt_version: str | None
    creation_cost_usd: float | None
    production_minutes: float | None

    def inputs(self) -> dict[str, float | None]:
        values: dict[str, float | None] = dict.fromkeys(SNAPSHOT_METRICS)
        if self.snapshot is not None:
            for key in SNAPSHOT_METRICS:
                raw = getattr(self.snapshot, key)
                values[key] = None if raw is None else float(raw)
        values["creation_cost_usd"] = self.creation_cost_usd
        values["production_minutes"] = self.production_minutes
        return values

    def counts(self) -> dict[str, int | None]:
        return {
            k: (getattr(self.snapshot, k) if self.snapshot is not None else None)
            for k in COUNT_METRICS
        }

    def posting_hour(self) -> int:
        """Local hour (campaign time zone) of the actual publish time."""
        when = self.publication.published_at or self.publication.scheduled_at
        try:
            zone = ZoneInfo(self.campaign.timezone or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            zone = ZoneInfo("UTC")
        return when.astimezone(zone).hour

    def attribute(self, name: str) -> str | None:
        values: dict[str, object | None] = {
            "topic": self.asset.topic,
            "pillar": self.asset.pillar,
            "hook_type": self.version.hook_type,
            "platform": self.asset.platform.value,
            "format": self.asset.format.value,
            "word_count_bucket": word_count_bucket(self.version.word_count),
            "cta_type": self.version.cta_type,
            "media_type": self.version.media_type,
            "posting_hour": self.posting_hour(),
            "campaign": self.campaign.id,
            "audience": self.asset.audience_id,
            "model": self.model,
            "prompt_version": self.prompt_version,
        }
        value = values[name]
        return None if value is None else str(value)


async def _load_rows(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    publication_id: uuid.UUID | None = None,
    platforms: Sequence[Platform] = (),
    since: datetime | None = None,
    until: datetime | None = None,
    sources: Sequence[MetricSource] = (),
) -> list[PublicationRow]:
    """PUBLISHED publications with their latest snapshot inside the window/source filter,
    content attributes, and the generation cost/time attributed to the asset."""
    snap = (
        select(AnalyticsSnapshot)
        .distinct(AnalyticsSnapshot.publication_id)
        .order_by(AnalyticsSnapshot.publication_id, AnalyticsSnapshot.captured_at.desc())
    )
    if since is not None:
        snap = snap.where(AnalyticsSnapshot.captured_at >= since)
    if until is not None:
        snap = snap.where(AnalyticsSnapshot.captured_at <= until)
    if sources:
        snap = snap.where(AnalyticsSnapshot.source.in_(list(sources)))
    latest = aliased(AnalyticsSnapshot, snap.subquery())
    stmt = (
        select(Publication, ContentAsset, ContentVersion, Campaign, latest, ModelRun.model)
        .join(ContentAsset, ContentAsset.id == Publication.content_asset_id)
        .join(ContentVersion, ContentVersion.id == Publication.content_version_id)
        .join(Campaign, Campaign.id == ContentAsset.campaign_id)
        .outerjoin(latest, latest.publication_id == Publication.id)
        .outerjoin(ModelRun, ModelRun.id == ContentVersion.model_run_id)
        .where(Publication.status == PublicationStatus.PUBLISHED)
        .order_by(Publication.scheduled_at, Publication.id)
        .limit(MAX_COMPARISON_ROWS + 1)
    )
    if brand_id is not None:
        stmt = stmt.where(Publication.brand_id == brand_id)
    if campaign_id is not None:
        stmt = stmt.where(ContentAsset.campaign_id == campaign_id)
    if publication_id is not None:
        stmt = stmt.where(Publication.id == publication_id)
    if platforms:
        stmt = stmt.where(ContentAsset.platform.in_(list(platforms)))
    result = (await session.execute(stmt)).all()
    if len(result) > MAX_COMPARISON_ROWS:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "too many publications in scope; narrow the filters",
            details={"limit": "max_rows", "max": MAX_COMPARISON_ROWS},
        )
    costs = await _generation_effort(session, {r[1].id for r in result})
    prompt_ids = {r[2].prompt_version_id for r in result if r[2].prompt_version_id}
    prompts: dict[uuid.UUID, str] = {}
    if prompt_ids:
        found = await session.execute(select(PromptVersion).where(PromptVersion.id.in_(prompt_ids)))
        for pv in found.scalars():
            prompts[pv.id] = f"{pv.domain}/{pv.name}@v{pv.version}"
    rows: list[PublicationRow] = []
    for pub, asset, version, campaign, snapshot, model in result:
        cost, minutes = costs.get(asset.id, (None, None))
        prompt = prompts.get(version.prompt_version_id) if version.prompt_version_id else None
        rows.append(
            PublicationRow(
                publication=pub,
                asset=asset,
                version=version,
                campaign=campaign,
                snapshot=snapshot,
                model=model,
                prompt_version=prompt,
                creation_cost_usd=cost,
                production_minutes=minutes,
            )
        )
    return rows


async def _generation_effort(
    session: AsyncSession, asset_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[float | None, float | None]]:
    """Cost (USD) and model time (minutes) of every model call made by the writer runs of
    the asset's versions, including repairs and regenerations. Planning/research calls are
    campaign-wide and NOT attributed to single posts (docs/METRICS.md). A value is NULL when
    any call's cost/latency is unknown (remote model without prices), never 0."""
    if not asset_ids:
        return {}
    runs = (
        select(ContentVersion.content_asset_id, ContentVersion.agent_run_id)
        .where(
            ContentVersion.content_asset_id.in_(asset_ids),
            ContentVersion.agent_run_id.is_not(None),
        )
        .distinct()
        .subquery()
    )
    stmt = (
        select(
            runs.c.content_asset_id,
            func.sum(ModelRun.estimated_cost_usd),
            func.bool_or(ModelRun.estimated_cost_usd.is_(None)),
            func.sum(ModelRun.latency_ms),
            func.bool_or(ModelRun.latency_ms.is_(None)),
        )
        .join(ModelRun, ModelRun.agent_run_id == runs.c.agent_run_id)
        .group_by(runs.c.content_asset_id)
    )
    effort: dict[uuid.UUID, tuple[float | None, float | None]] = {}
    for asset_id, cost, cost_unknown, latency, latency_unknown in (
        await session.execute(stmt)
    ).all():
        effort[asset_id] = (
            None if cost_unknown or cost is None else float(cost),
            None if latency_unknown or latency is None else float(latency) / 60_000,
        )
    return effort


def provenance(snapshots: Iterable[AnalyticsSnapshot]) -> DataProvenance:
    counts: dict[MetricSource, int] = {}
    for snapshot in snapshots:
        counts[snapshot.source] = counts.get(snapshot.source, 0) + 1
    simulated = any(s in SIMULATED_SOURCES for s in counts)
    real = any(s not in SIMULATED_SOURCES for s in counts)
    note = None
    if simulated and real:
        note = (
            "Mixes simulated (MOCK/FIXTURE) and real (API/MANUAL) data; "
            "filter with sources= before drawing conclusions."
        )
    elif simulated:
        note = "Simulated data (MOCK/FIXTURE): not real platform performance."
    return DataProvenance(sources=counts, simulated=simulated, real=real, note=note)


# ------------------------------------------------------------------ report (§16, §17)
def _publication_entry(
    row: PublicationRow, definitions: dict[str, metric_definitions.ResolvedDefinition]
) -> dict[str, Any]:
    inputs = row.inputs()
    snapshot = row.snapshot
    return {
        "publication_id": str(row.publication.id),
        "content_asset_id": str(row.asset.id),
        "snapshot_id": str(snapshot.id) if snapshot else None,
        "captured_at": snapshot.captured_at.isoformat() if snapshot else None,
        "source": snapshot.source.value if snapshot else None,
        # §16 content attributes
        "platform": row.asset.platform.value,
        "format": row.asset.format.value,
        "angle": row.asset.angle.value if row.asset.angle else None,
        "funnel_stage": row.asset.funnel_stage.value if row.asset.funnel_stage else None,
        "topic": row.asset.topic,
        "pillar": row.asset.pillar,
        "hook_type": row.version.hook_type,
        "cta_type": row.version.cta_type,
        "media_type": row.version.media_type,
        "word_count": row.version.word_count,
        "hook": row.version.hook,
        "posting_hour": row.posting_hour(),
        "model": row.model,
        "prompt_version": row.prompt_version,
        "metrics": row.counts(),
        "effort": {
            "creation_cost_usd": row.creation_cost_usd,
            "production_minutes": row.production_minutes,
        },
        "derived": {key: evaluate(d.formula, inputs).as_json() for key, d in definitions.items()},
    }


def _aggregate(
    group: list[PublicationRow], definitions: dict[str, metric_definitions.ResolvedDefinition]
) -> dict[str, Any]:
    inputs = [r.inputs() for r in group]
    return {
        "publications": len(group),
        "totals": sum_counts(r.counts() for r in group),
        "derived": {
            key: evaluate_aggregate(d.formula, inputs).as_json() for key, d in definitions.items()
        },
    }


async def campaign_report(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    sources: Sequence[MetricSource] = (),
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> CampaignReportRead:
    campaign = await _require_campaign(session, campaign_id)
    definitions = await metric_definitions.resolve(session, campaign.brand_id)
    rows = await _load_rows(
        session, campaign_id=campaign_id, since=since, until=until, sources=sources
    )
    with_data = [r for r in rows if r.snapshot is not None]
    by_platform: dict[str, list[PublicationRow]] = defaultdict(list)
    by_format: dict[str, list[PublicationRow]] = defaultdict(list)
    for row in with_data:
        by_platform[row.asset.platform.value].append(row)
        by_format[row.asset.format.value].append(row)
    publications = [_publication_entry(r, definitions) for r in rows]
    overall = _aggregate(with_data, definitions)
    ranking_key = "engagement_rate"
    ranked = sorted(
        (p for p in publications if p["derived"].get(ranking_key, {}).get("value") is not None),
        key=lambda p: (-p["derived"][ranking_key]["value"], p["publication_id"]),
    )
    half = len(ranked) // 2
    return CampaignReportRead(
        campaign_id=campaign_id,
        range={"since": since, "until": until},
        coverage={"published": len(rows), "with_snapshot": len(with_data)},
        totals=overall["totals"],
        derived=overall["derived"],
        by_platform={k: _aggregate(v, definitions) for k, v in sorted(by_platform.items())},
        by_format={k: _aggregate(v, definitions) for k, v in sorted(by_format.items())},
        publications=publications,
        definitions=[
            {
                **d.citation(),
                "name": d.name,
                "formula": d.formula.source,
                "weights": dict(d.formula.weights),
                "scope": "brand" if d.brand_id else "default",
            }
            for d in definitions.values()
        ],
        provenance=provenance(r.snapshot for r in with_data if r.snapshot is not None),
        sufficiency={
            "min_sample": min_sample,
            "publications_with_data": len(with_data),
            "satisfied": len(with_data) >= min_sample,
            "ranking_metric": ranking_key,
            # Top/bottom never overlap: each takes at most half of the ranked publications.
            "top_publication_ids": [p["publication_id"] for p in ranked[: min(3, half)]],
            "bottom_publication_ids": [p["publication_id"] for p in ranked[::-1][: min(3, half)]],
        },
    )


# ------------------------------------------------------------------ per publication
async def publication_metrics(
    session: AsyncSession, publication_id: uuid.UUID, definition_id: uuid.UUID | None = None
) -> PublicationMetricsRead:
    publication = await session.get(Publication, publication_id)
    if publication is None:
        raise _not_found("publication")
    if definition_id is not None:
        chosen = await metric_definitions.get_definition(session, definition_id)
        definitions = {chosen.key: chosen}
    else:
        definitions = await metric_definitions.resolve(session, publication.brand_id)
    rows = await _load_rows(session, publication_id=publication_id)
    if not rows:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "publication is not PUBLISHED; it has no metrics",
            details={"status": publication.status.value},
        )
    row = rows[0]
    inputs = row.inputs()
    derived: list[DerivedValueRead] = []
    for d in definitions.values():
        result = evaluate(d.formula, inputs)
        derived.append(
            DerivedValueRead(
                definition_id=d.id,
                key=d.key,
                revision=d.version_no,
                value=None if result.value is None else round(result.value, 6),
                null_reason=result.null_reason,
                null_code=result.null_code,
                missing_inputs=list(result.missing_inputs),
            )
        )
    return PublicationMetricsRead(
        publication_id=publication_id,
        latest_snapshot_id=row.snapshot.id if row.snapshot else None,
        raw=AnalyticsSnapshotRead.model_validate(row.snapshot) if row.snapshot else None,
        inputs=inputs,
        derived=derived,
        provenance=provenance([row.snapshot] if row.snapshot else []),
    )


# ------------------------------------------------------------------ attribute comparisons
async def attribute_comparisons(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID,
    attribute: str,
    metric: str,
    platforms: Sequence[Platform] = (),
    campaign_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    sources: Sequence[MetricSource] = (),
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> AttributeComparisonRead:
    """Group publications by one §16 attribute; per group n, mean, median of one metric.

    ``metric`` is a derived-metric key (active definition for the brand) or a raw input
    (a snapshot count, ``creation_cost_usd``, ``production_minutes``). Publications whose
    metric is NULL are counted in ``excluded_null`` and never enter the statistics;
    publications without any snapshot in the window are left out entirely.
    """
    if attribute not in ATTRIBUTES:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            f"unknown attribute {attribute!r}",
            details={"field": "attribute", "allowed": list(ATTRIBUTES)},
        )
    if await session.get(Brand, brand_id) is None:
        raise _not_found("brand")
    definitions = await metric_definitions.resolve(session, brand_id)
    definition = definitions.get(metric)
    if definition is None and metric not in FORMULA_METRICS:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            f"unknown metric {metric!r}",
            details={"field": "metric", "allowed": sorted(definitions) + sorted(FORMULA_METRICS)},
        )
    rows = await _load_rows(
        session,
        brand_id=brand_id,
        campaign_id=campaign_id,
        platforms=platforms,
        since=since,
        until=until,
        sources=sources,
    )
    groups: dict[str | None, list[tuple[float, AnalyticsSnapshot]]] = defaultdict(list)
    excluded: dict[str | None, int] = defaultdict(int)
    with_data: list[AnalyticsSnapshot] = []
    for row in rows:
        if row.snapshot is None:
            continue
        with_data.append(row.snapshot)
        key = row.attribute(attribute)
        inputs = row.inputs()
        value = (
            evaluate(definition.formula, inputs).value
            if definition is not None
            else inputs.get(metric)
        )
        if value is None:
            excluded[key] += 1
        else:
            groups[key].append((float(value), row.snapshot))
    out: list[AttributeGroupRead] = []
    for key in sorted(set(groups) | set(excluded), key=lambda k: (k is None, k or "")):
        members = groups.get(key, [])
        values = [v for v, _ in members]
        source_counts: dict[MetricSource, int] = {}
        for _, snapshot in members:
            source_counts[snapshot.source] = source_counts.get(snapshot.source, 0) + 1
        out.append(
            AttributeGroupRead(
                value=key,
                n=len(values),
                excluded_null=excluded.get(key, 0),
                mean=statistics.fmean(values) if values else None,
                median=statistics.median(values) if values else None,
                sufficient=len(values) >= min_sample,
                snapshot_ids=[s.id for _, s in members],
                sources=source_counts,
            )
        )
    cited = (
        {**definition.citation(), "formula": definition.formula.source}
        if definition is not None
        else None
    )
    return AttributeComparisonRead(
        attribute=attribute,
        metric=metric,
        definition=cited,
        filters={
            "brand_id": str(brand_id),
            "campaign_id": str(campaign_id) if campaign_id else None,
            "platform": [p.value for p in platforms],
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "sources": [s.value for s in sources],
        },
        min_sample=min_sample,
        groups=out,
        provenance=provenance(with_data),
        note=CORRELATION_NOTE,
    )


SYSTEM_ANALYTICS = Actor(ActorType.SYSTEM, "analytics")
SCHEDULER_ANALYTICS = Actor(ActorType.SCHEDULER, "scheduler:analytics-pull")
