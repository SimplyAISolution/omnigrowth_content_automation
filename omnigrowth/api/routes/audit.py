"""``/api/v1/audit/timeline/{entity_type}/{entity_id}`` — who changed what, in order (§37)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from omnigrowth.api.deps import SessionDep, ViewerDep
from omnigrowth.schemas.common import Page
from omnigrowth.services import audit_timeline

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/timeline/{entity_type}/{entity_id}", response_model=Page[dict[str, Any]])
async def timeline(
    entity_type: Annotated[str, Path(pattern=r"^[a-z_]{3,40}$")],
    entity_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> Page[dict[str, Any]]:
    items = await audit_timeline.timeline(session, entity_type, entity_id, limit)
    return Page[dict[str, Any]](items=items)
