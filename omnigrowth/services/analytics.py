"""Analytics (spec §16-17, §34): mock pulls, fixture ingest, campaign report.

Snapshots are immutable and NULL means "not captured" (never 0). The report joins each
publication's latest snapshot to the content attributes it was produced with (§16), so
the Optimization Agent can compare outcomes against platform, format, angle, hook type...
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.analytics.metrics import (
    COUNT_METRICS,
    DEFINITIONS,
    derive_all,
    derive_all_aggregate,
    sum_counts,
)
from omnigrowth.core.enums import (
    ActorType,
    JobKind,
    MetricSource,
    PublicationStatus,
    PublishMode,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.state_machines import Actor
from omnigrowth.models import (
    AnalyticsSnapshot,
    Campaign,
    ContentAsset,
    ContentVersion,
    Publication,
)
from omnigrowth.publishing.mock import MockPublisher
from omnigrowth.schemas.analytics import (
    AnalyticsSnapshotBatchCreate,
    AnalyticsSnapshotIngestResultRead,
    AnalyticsSnapshotRead,
    CampaignReportRead,
)
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


# ------------------------------------------------------------------ report (§16, §17)
def _counts(snapshot: AnalyticsSnapshot) -> dict[str, int | None]:
    return {k: getattr(snapshot, k) for k in COUNT_METRICS}


def _derived_json(values: dict[str, int | None]) -> dict[str, dict[str, Any]]:
    return {k: v.as_json() for k, v in derive_all(values).items()}


def _aggregate_json(rows: list[dict[str, int | None]]) -> dict[str, dict[str, Any]]:
    return {k: v.as_json() for k, v in derive_all_aggregate(list(rows)).items()}


async def campaign_report(session: AsyncSession, campaign_id: uuid.UUID) -> CampaignReportRead:
    await _require_campaign(session, campaign_id)
    latest = (
        select(
            AnalyticsSnapshot.publication_id,
            func.max(AnalyticsSnapshot.captured_at).label("captured_at"),
        )
        .group_by(AnalyticsSnapshot.publication_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Publication, ContentAsset, ContentVersion, AnalyticsSnapshot)
            .join(ContentAsset, ContentAsset.id == Publication.content_asset_id)
            .join(ContentVersion, ContentVersion.id == Publication.content_version_id)
            .outerjoin(latest, latest.c.publication_id == Publication.id)
            .outerjoin(
                AnalyticsSnapshot,
                (AnalyticsSnapshot.publication_id == Publication.id)
                & (AnalyticsSnapshot.captured_at == latest.c.captured_at),
            )
            .where(
                ContentAsset.campaign_id == campaign_id,
                Publication.status == PublicationStatus.PUBLISHED,
            )
            .order_by(Publication.scheduled_at, Publication.id)
        )
    ).all()
    publications: list[dict[str, Any]] = []
    with_data: list[dict[str, int | None]] = []
    platform_rows: dict[str, list[dict[str, int | None]]] = defaultdict(list)
    for pub, asset, version, snapshot in rows:
        counts = _counts(snapshot) if snapshot is not None else dict.fromkeys(COUNT_METRICS)
        publications.append(
            {
                "publication_id": str(pub.id),
                "content_asset_id": str(asset.id),
                "snapshot_id": str(snapshot.id) if snapshot is not None else None,
                "captured_at": snapshot.captured_at.isoformat() if snapshot else None,
                # §16 content attributes
                "platform": asset.platform.value,
                "format": asset.format.value,
                "angle": asset.angle.value if asset.angle else None,
                "funnel_stage": asset.funnel_stage.value if asset.funnel_stage else None,
                "hook_type": version.hook_type,
                "cta_type": version.cta_type,
                "word_count": version.word_count,
                "hook": version.hook,
                "posting_hour": pub.scheduled_at.hour,
                "metrics": counts,
                "derived": _derived_json(counts),
            }
        )
        if snapshot is not None:
            with_data.append(counts)
            platform_rows[asset.platform.value].append(counts)
    totals = sum_counts(with_data)
    by_platform = {
        platform: {
            "publications": len(items),
            "totals": sum_counts(items),
            "derived": _aggregate_json(items),
        }
        for platform, items in sorted(platform_rows.items())
    }
    return CampaignReportRead(
        campaign_id=campaign_id,
        coverage={"published": len(rows), "with_snapshot": len(with_data)},
        totals=totals,
        derived=_aggregate_json(with_data),
        by_platform=by_platform,
        publications=publications,
        definitions=[
            {
                "key": d.key,
                "name": d.name,
                "numerator": list(d.numerator),
                "denominator": d.denominator,
            }
            for d in DEFINITIONS
        ],
    )


SYSTEM_ANALYTICS = Actor(ActorType.SYSTEM, "analytics")
