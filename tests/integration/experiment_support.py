"""Test factory for experiments: many posts with controlled metrics, without generation.

Rows are inserted directly (tests only; the application writes status via the state
machines). A "post" = content asset + version + succeeded publication job + PUBLISHED
publication on one platform account.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import (
    ActorType,
    ContentAssetStatus,
    ContentFormat,
    ContentVersionOrigin,
    MetricSource,
    Platform,
    PublicationJobStatus,
    PublicationStatus,
    PublishMode,
)
from omnigrowth.core.ids import uuid7
from omnigrowth.models import (
    AnalyticsSnapshot,
    ContentAsset,
    ContentVersion,
    Publication,
    PublicationJob,
)


async def make_assets(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID,
    campaign_id: uuid.UUID,
    platform: Platform,
    n: int,
) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for i in range(n):
        asset_id = uuid7()
        session.add(
            ContentAsset(
                id=asset_id,
                brand_id=brand_id,
                campaign_id=campaign_id,
                root_asset_id=asset_id,
                platform=platform,
                format=ContentFormat.TEXT_POST,
                topic=f"experiment post {i}",
            )
        )
        ids.append(asset_id)
    await session.commit()
    return ids


async def publish(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID,
    asset_id: uuid.UUID,
    account_id: uuid.UUID,
    platform: Platform,
    published_at: datetime,
    mode: PublishMode = PublishMode.MANUAL,
) -> uuid.UUID:
    """``mode=MANUAL``: a post a human actually published (real audience); ``MOCK``: the
    mock publisher (no audience; its metrics are simulated whatever their source)."""
    version_id, job_id, publication_id = uuid7(), uuid7(), uuid7()
    body = f"post {asset_id}"
    session.add(
        ContentVersion(
            id=version_id,
            content_asset_id=asset_id,
            version_no=1,
            origin=ContentVersionOrigin.HUMAN_EDIT,
            body=body,
            body_hash=hashlib.sha256(body.encode()).hexdigest(),
            word_count=2,
            char_count=len(body),
            author_actor_type=ActorType.HUMAN,
            author_actor_id="test",
        )
    )
    await session.flush()
    session.add(
        PublicationJob(
            id=job_id,
            brand_id=brand_id,
            content_asset_id=asset_id,
            content_version_id=version_id,
            platform_account_id=account_id,
            idempotency_key=hashlib.sha256(str(job_id).encode()).hexdigest(),
            due_at=published_at,
            schedule_timezone="UTC",
            correlation_id="test",
            actor_type=ActorType.HUMAN,
            actor_id="test",
            status=PublicationJobStatus.SUCCEEDED,
            publish_mode=mode,
        )
    )
    await session.flush()
    session.add(
        Publication(
            id=publication_id,
            brand_id=brand_id,
            publication_job_id=job_id,
            content_asset_id=asset_id,
            content_version_id=version_id,
            platform_account_id=account_id,
            platform=platform,
            publish_mode=mode,
            scheduled_at=published_at,
            published_at=published_at,
            status=PublicationStatus.PUBLISHED,
            external_post_id=f"mock_{publication_id.hex[:12]}",
        )
    )
    await session.execute(
        update(ContentAsset)
        .where(ContentAsset.id == asset_id)
        .values(status=ContentAssetStatus.PUBLISHED)
    )
    await session.commit()
    return publication_id


async def snapshot(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID,
    publication_id: uuid.UUID,
    captured_at: datetime,
    source: MetricSource,
    **metrics: int | None,
) -> uuid.UUID:
    snapshot_id = uuid7()
    session.add(
        AnalyticsSnapshot(
            id=snapshot_id,
            brand_id=brand_id,
            publication_id=publication_id,
            captured_at=captured_at,
            source=source,
            **metrics,
        )
    )
    await session.commit()
    return snapshot_id
