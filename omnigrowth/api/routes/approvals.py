"""``/api/v1/approvals`` — the review queue and the seven §37 approval actions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from omnigrowth.api.deps import EditorDep, JobQueueDep, SessionDep, ViewerDep
from omnigrowth.api.idempotency import Outcome, idempotent
from omnigrowth.api.routes.jobs import to_read
from omnigrowth.core.enums import ApprovalActionType, ContentAssetStatus
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.content import ContentAssetRead
from omnigrowth.schemas.publishing import (
    ApprovalActionRead,
    ApprovalActionRequest,
    ApprovalActionResultRead,
)
from omnigrowth.services import content as content_service
from omnigrowth.services import jobs as job_service
from omnigrowth.services import publishing as publishing_service

router = APIRouter(prefix="/approvals", tags=["approvals"])

# API_PLAN §2.8: [IK] required for the actions with external or job effects.
_IK_REQUIRED = {
    ApprovalActionType.SCHEDULE,
    ApprovalActionType.PUBLISH_NOW,
    ApprovalActionType.REGENERATE,
}
_ACCEPTED = {ApprovalActionType.PUBLISH_NOW, ApprovalActionType.REGENERATE}


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


@router.post(
    "/{asset_id}/actions",
    response_model=ApprovalActionResultRead,
    responses={202: {"model": ApprovalActionResultRead}},
)
async def approval_action(
    request: Request,
    asset_id: uuid.UUID,
    body: ApprovalActionRequest,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> JSONResponse:
    """APPROVE / REJECT / REQUEST_REVISION / EDIT / REGENERATE / SCHEDULE / PUBLISH_NOW.
    SCHEDULE and PUBLISH_NOW only queue a publication job; the scheduler publishes."""

    async def run() -> Outcome:
        outcome = await publishing_service.apply_action(
            session, queue, principal.actor, asset_id, body
        )
        result = ApprovalActionResultRead(
            asset=await content_service.get_asset(session, asset_id),
            publication_job=(
                await publishing_service.get_job(session, outcome.publication_job_id)
                if outcome.publication_job_id
                else None
            ),
            job=to_read(await job_service.get_job(session, outcome.job_id))
            if outcome.job_id
            else None,
            version=(
                await content_service.get_version(session, asset_id, outcome.version_id)
                if outcome.version_id
                else None
            ),
        )
        headers = {"X-Job-ID": str(outcome.job_id)} if outcome.job_id else {}
        return (202 if body.action in _ACCEPTED else 200), result, headers

    return await idempotent(
        request,
        principal,
        {"asset_id": asset_id, **body.model_dump()},
        run,
        required=body.action in _IK_REQUIRED,
    )


@router.get("/{asset_id}/history", response_model=Page[ApprovalActionRead])
async def approval_history(
    asset_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> Page[ApprovalActionRead]:
    items = await publishing_service.approval_history(session, asset_id)
    return Page[ApprovalActionRead](items=items)
