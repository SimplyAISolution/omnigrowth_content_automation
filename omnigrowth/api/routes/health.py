"""``GET /api/v1/health`` (API_PLAN §2.1). Unauthenticated."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from omnigrowth.core.enums import HealthStatus
from omnigrowth.core.settings import Settings, get_settings
from omnigrowth.schemas.health import HealthRead
from omnigrowth.services import health as health_service

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthRead,
    responses={503: {"model": HealthRead, "description": "Database or Redis is down"}},
)
async def get_health(
    response: Response, settings: Annotated[Settings, Depends(get_settings)]
) -> HealthRead:
    report = await health_service.get_health(settings)
    if report.status is HealthStatus.DOWN:
        response.status_code = 503
    return HealthRead.from_report(report, datetime.now(UTC))
