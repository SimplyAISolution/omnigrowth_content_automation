"""``/api/v1/campaigns`` — create / wizard, planning job, activation, planning outputs."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from omnigrowth.api.deps import EditorDep, JobQueueDep, SessionDep, ViewerDep
from omnigrowth.api.idempotency import Outcome, idempotent
from omnigrowth.api.routes.jobs import to_read
from omnigrowth.core.enums import AngleType, CampaignStatus, ContentIdeaStatus, Platform
from omnigrowth.schemas.campaigns import (
    CampaignCreate,
    CampaignRead,
    CampaignStrategyRead,
    CampaignWizardRequest,
    CampaignWizardResultRead,
    ContentIdeaRead,
    PlanItemRead,
    ResearchPacketRead,
)
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.content import AssetGenerateRequest
from omnigrowth.schemas.sources import JobRead
from omnigrowth.services import campaigns as campaign_service
from omnigrowth.services import content as content_service
from omnigrowth.services import jobs as job_service

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("", response_model=Page[CampaignRead])
async def list_campaigns(
    _principal: ViewerDep,
    session: SessionDep,
    brand_id: uuid.UUID | None = None,
    status_filter: Annotated[list[CampaignStatus] | None, Query(alias="status")] = None,
) -> Page[CampaignRead]:
    items = await campaign_service.list_campaigns(
        session, brand_id=brand_id, statuses=status_filter or ()
    )
    return Page[CampaignRead](items=items)


@router.post("", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CampaignCreate, principal: EditorDep, session: SessionDep
) -> CampaignRead:
    return await campaign_service.create_campaign(session, principal.actor, body)


@router.post(
    "/wizard",
    response_model=CampaignWizardResultRead,
    status_code=status.HTTP_201_CREATED,
    responses={202: {"model": CampaignWizardResultRead, "description": "Planning enqueued"}},
)
async def campaign_wizard(
    request: Request,
    body: CampaignWizardRequest,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> JSONResponse:
    async def run() -> Outcome:
        campaign, source_id, job_id = await campaign_service.run_wizard(
            session, queue, principal.actor, body
        )
        job = to_read(await job_service.get_job(session, job_id)) if job_id else None
        result = CampaignWizardResultRead(campaign=campaign, source_asset_id=source_id, job=job)
        return (202 if job else 201), result, {}

    # [IK] when it starts planning (a job); optional for a plain create.
    return await idempotent(request, principal, body, run, required=body.generate)


@router.get("/{campaign_id}", response_model=CampaignRead)
async def get_campaign(
    campaign_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> CampaignRead:
    return await campaign_service.get_campaign(session, campaign_id)


@router.post("/{campaign_id}/generate", response_model=JobRead, status_code=202)
async def generate_plan(
    request: Request,
    campaign_id: uuid.UUID,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> JSONResponse:
    """Planning pipeline only (research -> strategy -> ideas -> plan); never assets."""

    async def run() -> Outcome:
        job_id = await campaign_service.start_planning(session, queue, principal.actor, campaign_id)
        return 202, to_read(await job_service.get_job(session, job_id)), {}

    return await idempotent(request, principal, {"campaign_id": campaign_id}, run)


@router.post("/{campaign_id}/activate", response_model=CampaignRead)
async def activate_campaign(
    campaign_id: uuid.UUID, principal: EditorDep, session: SessionDep
) -> CampaignRead:
    return await campaign_service.activate(session, principal.actor, campaign_id)


@router.post("/{campaign_id}/generate-assets", response_model=JobRead, status_code=202)
async def generate_assets(
    request: Request,
    campaign_id: uuid.UUID,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
    body: AssetGenerateRequest | None = None,
) -> JSONResponse:
    """ACTIVE campaigns only: DRAFT assets per plan item (CA1) + one generation/QA job.
    Plan items without an MVP writer are listed in the job result as ``skipped`` (D2)."""
    plan_item_ids = body.plan_item_ids if body is not None else None

    async def run() -> Outcome:
        job_id = await content_service.generate_assets(
            session, queue, principal.actor, campaign_id, plan_item_ids
        )
        return 202, to_read(await job_service.get_job(session, job_id)), {}

    inputs = {"campaign_id": campaign_id, "plan_item_ids": plan_item_ids}
    return await idempotent(request, principal, inputs, run)


@router.get("/{campaign_id}/strategy", response_model=CampaignStrategyRead)
async def get_strategy(
    campaign_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> CampaignStrategyRead:
    return await campaign_service.get_strategy(session, campaign_id)


@router.get("/{campaign_id}/research-packet", response_model=ResearchPacketRead)
async def get_research_packet(
    campaign_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> ResearchPacketRead:
    return await campaign_service.get_research_packet(session, campaign_id)


@router.get("/{campaign_id}/ideas", response_model=Page[ContentIdeaRead])
async def list_ideas(
    campaign_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    status_filter: Annotated[list[ContentIdeaStatus] | None, Query(alias="status")] = None,
    angle_type: Annotated[list[AngleType] | None, Query()] = None,
    platform: Annotated[list[Platform] | None, Query()] = None,
) -> Page[ContentIdeaRead]:
    items = await campaign_service.list_ideas(
        session,
        campaign_id,
        statuses=status_filter or (),
        angles=angle_type or (),
        platforms=platform or (),
    )
    return Page[ContentIdeaRead](items=items)


@router.get("/{campaign_id}/plan", response_model=Page[PlanItemRead])
async def get_plan(
    campaign_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    horizon_days: Annotated[int, Query()] = campaign_service.PLAN_HORIZON_DAYS,
) -> Page[PlanItemRead]:
    items = await campaign_service.get_plan(session, campaign_id, horizon_days)
    return Page[PlanItemRead](items=items)
