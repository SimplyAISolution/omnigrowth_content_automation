"""``/api/v1/calendar`` (spec §13, API_PLAN §2.7): read-only calendar view."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from omnigrowth.api.deps import SessionDep, ViewerDep
from omnigrowth.core.enums import ContentAssetStatus, Platform
from omnigrowth.schemas.analytics import CalendarRead
from omnigrowth.services import calendar as calendar_service

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("", response_model=CalendarRead)
async def get_calendar(
    _principal: ViewerDep,
    session: SessionDep,
    date: date,
    tz: str,
    view: Annotated[str, Query(pattern="^(day|week|month)$")] = "week",
    brand_id: uuid.UUID | None = None,
    platform: Annotated[list[Platform] | None, Query()] = None,
    campaign_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    status: Annotated[list[ContentAssetStatus] | None, Query()] = None,
    include_unscheduled_plan_items: bool = True,
) -> CalendarRead:
    """Publication jobs (by ``due_at``) plus plan items without a job, in ``tz``."""
    return await calendar_service.get_calendar(
        session,
        view=view,
        day=date,
        tz=tz,
        brand_id=brand_id,
        platforms=platform or (),
        campaign_ids=campaign_id or (),
        statuses=status or (),
        include_unscheduled_plan_items=include_unscheduled_plan_items,
    )
