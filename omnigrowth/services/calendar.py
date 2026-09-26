"""Calendar view (spec §13, API_PLAN §2.7): publication jobs by ``due_at`` plus plan items
that have no publication job yet, in a range computed from ``view`` + ``date`` in ``tz``.

Read-only. Rescheduling stays in the approvals flow (§37 SCHEDULE / CA12), which already
cancels and replaces the pending job; the plan-item "move" endpoint is not implemented yet.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import (
    CalendarItemKind,
    ContentAssetStatus,
    Platform,
    PublicationJobStatus,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.models import Campaign, ContentAsset, ContentVersion, PlanItem, PublicationJob
from omnigrowth.schemas.analytics import CalendarItemRead, CalendarRead

MAX_ITEMS = 2_000
VIEWS = ("day", "week", "month")
# A job replaced by a reschedule/edit is history, not a calendar card.
HIDDEN_JOB_STATUSES = (PublicationJobStatus.CANCELLED,)


def _invalid(message: str, **details: object) -> OmniGrowthError:
    return OmniGrowthError(ErrorCode.VALIDATION_FAILURE, message, details=dict(details))


def calendar_range(view: str, day: date, tz: str) -> tuple[datetime, datetime]:
    """Local-midnight bounds in ``tz``; weeks start on Monday (ISO 8601)."""
    if view not in VIEWS:
        raise _invalid(f"view must be one of {VIEWS}", field="view")
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise _invalid(f"unknown time zone {tz!r}", field="tz") from exc
    if view == "day":
        first, last = day, day + timedelta(days=1)
    elif view == "week":
        first = day - timedelta(days=day.weekday())
        last = first + timedelta(days=7)
    else:
        first = day.replace(day=1)
        last = (first + timedelta(days=32)).replace(day=1)
    return (
        datetime.combine(first, time.min, tzinfo=zone),
        datetime.combine(last, time.min, tzinfo=zone),
    )


async def get_calendar(
    session: AsyncSession,
    *,
    view: str,
    day: date,
    tz: str,
    brand_id: uuid.UUID | None = None,
    platforms: Sequence[Platform] = (),
    campaign_ids: Sequence[uuid.UUID] = (),
    statuses: Sequence[ContentAssetStatus] = (),
    include_unscheduled_plan_items: bool = True,
) -> CalendarRead:
    start, end = calendar_range(view, day, tz)

    jobs = (
        select(PublicationJob, ContentAsset, ContentVersion, Campaign)
        .join(ContentAsset, ContentAsset.id == PublicationJob.content_asset_id)
        .join(ContentVersion, ContentVersion.id == PublicationJob.content_version_id)
        .join(Campaign, Campaign.id == ContentAsset.campaign_id)
        .where(
            PublicationJob.due_at >= start,
            PublicationJob.due_at < end,
            PublicationJob.status.not_in(HIDDEN_JOB_STATUSES),
        )
        .order_by(PublicationJob.due_at, PublicationJob.id)
        .limit(MAX_ITEMS + 1)
    )
    has_job = exists().where(
        and_(
            PublicationJob.content_asset_id == ContentAsset.id,
            PublicationJob.status.not_in(HIDDEN_JOB_STATUSES),
        )
    )
    plans = (
        select(PlanItem, ContentAsset, Campaign)
        .join(Campaign, Campaign.id == PlanItem.campaign_id)
        .outerjoin(ContentAsset, ContentAsset.plan_item_id == PlanItem.id)
        .where(
            PlanItem.window_start >= start,
            PlanItem.window_start < end,
            PlanItem.archived_at.is_(None),
            ~has_job,
        )
        .order_by(PlanItem.window_start, PlanItem.id)
        .limit(MAX_ITEMS + 1)
    )
    if brand_id is not None:
        jobs = jobs.where(PublicationJob.brand_id == brand_id)
        plans = plans.where(PlanItem.brand_id == brand_id)
    if platforms:
        jobs = jobs.where(ContentAsset.platform.in_(list(platforms)))
        plans = plans.where(PlanItem.platform.in_(list(platforms)))
    if campaign_ids:
        jobs = jobs.where(ContentAsset.campaign_id.in_(list(campaign_ids)))
        plans = plans.where(PlanItem.campaign_id.in_(list(campaign_ids)))
    if statuses:
        jobs = jobs.where(ContentAsset.status.in_(list(statuses)))
        plans = plans.where(ContentAsset.status.in_(list(statuses)))

    items: list[CalendarItemRead] = []
    for job, asset, version, campaign in (await session.execute(jobs)).all():
        items.append(
            CalendarItemRead(
                kind=CalendarItemKind.PUBLICATION_JOB,
                id=job.id,
                title=version.title
                or version.hook
                or f"{asset.platform.value} {asset.format.value}",
                platform=asset.platform,
                format=asset.format,
                content_asset_id=asset.id,
                content_asset_status=asset.status,
                publication_job_status=job.status,
                due_at=job.due_at,
                window_start=None,
                window_end=None,
                campaign_id=campaign.id,
                campaign_name=campaign.name,
            )
        )
    if include_unscheduled_plan_items:
        for plan, asset, campaign in (await session.execute(plans)).all():
            items.append(
                CalendarItemRead(
                    kind=CalendarItemKind.PLAN_ITEM,
                    id=plan.id,
                    title=f"{plan.angle.value.title()} {plan.format.value.lower()}",
                    platform=plan.platform,
                    format=plan.format,
                    content_asset_id=asset.id if asset is not None else None,
                    content_asset_status=asset.status if asset is not None else None,
                    publication_job_status=None,
                    due_at=None,
                    window_start=plan.window_start,
                    window_end=plan.window_end,
                    campaign_id=campaign.id,
                    campaign_name=campaign.name,
                )
            )
    if len(items) > MAX_ITEMS:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "calendar range holds too many items; narrow the filters",
            details={"limit": "max_items", "max": MAX_ITEMS},
        )
    items.sort(key=lambda i: (i.due_at or i.window_start or start, str(i.id)))
    return CalendarRead(view=view, tz=tz, range_start=start, range_end=end, items=items)
