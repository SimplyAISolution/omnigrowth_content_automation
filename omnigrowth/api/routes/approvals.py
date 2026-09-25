"""``/api/v1/approvals`` — the review queue and the §37 approval actions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from omnigrowth.api.deps import EditorDep, SessionDep, ViewerDep
from omnigrowth.core.enums import ContentAssetStatus
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.content import ContentAssetRead
from omnigrowth.schemas.publishing import (
    ApprovalActionRead,
    ApprovalActionRequest,
    ApprovalActionResultRead,
)
from omnigrowth.services import content as content_service
from omnigrowth.services import publishing as publishing_service

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=Page[ContentAssetRead])
async def approval_queue(
    _principal: ViewerDep,
    session: SessionDep,
    brand_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    status: ContentAssetStatus = ContentAssetStatus.AWAITING_APPROVAL,
) -> Page[ContentAssetRead]:
    items = await publishing_service.approval_queue(
        session, brand_id=brand_id, campaign_id=campaign_id, status=status
    )
    return Page[ContentAssetRead](items=items)


@router.post("/{asset_id}/actions", response_model=ApprovalActionResultRead)
async def approval_action(
    asset_id: uuid.UUID, body: ApprovalActionRequest, principal: EditorDep, session: SessionDep
) -> ApprovalActionResultRead:
    """APPROVE / REJECT / REQUEST_REVISION / SCHEDULE / PUBLISH_NOW, recorded with the actor.
    SCHEDULE and PUBLISH_NOW only queue a publication job; the scheduler publishes."""
    _, job_id = await publishing_service.apply_action(session, principal.actor, asset_id, body)
    job = await publishing_service.get_job(session, job_id) if job_id else None
    asset = await content_service.get_asset(session, asset_id)
    return ApprovalActionResultRead(asset=asset, publication_job=job)


@router.get("/{asset_id}/history", response_model=Page[ApprovalActionRead])
async def approval_history(
    asset_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> Page[ApprovalActionRead]:
    items = await publishing_service.approval_history(session, asset_id)
    return Page[ApprovalActionRead](items=items)
