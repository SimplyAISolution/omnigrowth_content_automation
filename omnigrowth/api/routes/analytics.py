"""``/api/v1/analytics`` and ``/api/v1/recommendations`` (§16, §17, §34, §55 steps 14-16)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from omnigrowth.api.deps import EditorDep, JobQueueDep, SessionDep, ViewerDep
from omnigrowth.api.idempotency import Outcome, idempotent
from omnigrowth.api.routes.jobs import to_read
from omnigrowth.schemas.analytics import (
    AnalyticsPullRequest,
    AnalyticsSnapshotBatchCreate,
    AnalyticsSnapshotIngestResultRead,
    AnalyticsSnapshotRead,
    CampaignReportRead,
    RecommendationGenerateRequest,
    RecommendationRead,
)
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.sources import JobRead
from omnigrowth.services import analytics as analytics_service
from omnigrowth.services import jobs as job_service
from omnigrowth.services import recommendations as recommendation_service

router = APIRouter(prefix="/analytics", tags=["analytics"])
recommendations_router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post("/pull", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def pull(
    request: Request,
    body: AnalyticsPullRequest,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> JSONResponse:
    """Fetch metrics for the campaign's published posts (mock publisher in the MVP)."""

    async def run() -> Outcome:
        job_id = await analytics_service.request_pull(
            session, queue, principal.actor, body.campaign_id
        )
        return 202, to_read(await job_service.get_job(session, job_id)), {}

    return await idempotent(request, principal, body, run)


@router.post(
    "/snapshots",
    response_model=AnalyticsSnapshotIngestResultRead,
    status_code=status.HTTP_201_CREATED,
)
async def ingest(
    body: AnalyticsSnapshotBatchCreate, principal: EditorDep, session: SessionDep
) -> AnalyticsSnapshotIngestResultRead:
    return await analytics_service.ingest_snapshots(session, principal.actor, body)


@router.get("/snapshots", response_model=Page[AnalyticsSnapshotRead])
async def list_snapshots(
    campaign_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> Page[AnalyticsSnapshotRead]:
    items = await analytics_service.list_snapshots(session, campaign_id)
    return Page[AnalyticsSnapshotRead](items=items)


@router.get("/snapshots/{snapshot_id}", response_model=AnalyticsSnapshotRead)
async def get_snapshot(
    snapshot_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> AnalyticsSnapshotRead:
    return await analytics_service.get_snapshot(session, snapshot_id)


@router.get("/campaigns/{campaign_id}/report", response_model=CampaignReportRead)
async def campaign_report(
    campaign_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> CampaignReportRead:
    return await analytics_service.campaign_report(session, campaign_id)


@recommendations_router.post(
    "/generate", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED
)
async def generate_recommendations(
    request: Request,
    body: RecommendationGenerateRequest,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> JSONResponse:
    async def run() -> Outcome:
        job_id = await recommendation_service.request_generation(
            session, queue, principal.actor, body.campaign_id
        )
        return 202, to_read(await job_service.get_job(session, job_id)), {}

    return await idempotent(request, principal, body, run)


@recommendations_router.post("/{recommendation_id}/accept", response_model=RecommendationRead)
async def accept_recommendation(
    recommendation_id: uuid.UUID, principal: EditorDep, session: SessionDep
) -> RecommendationRead:
    return await recommendation_service.decide(
        session, principal.actor, recommendation_id, "accept"
    )


@recommendations_router.post("/{recommendation_id}/dismiss", response_model=RecommendationRead)
async def dismiss_recommendation(
    recommendation_id: uuid.UUID, principal: EditorDep, session: SessionDep
) -> RecommendationRead:
    return await recommendation_service.decide(
        session, principal.actor, recommendation_id, "dismiss"
    )


@recommendations_router.get("", response_model=Page[RecommendationRead])
async def list_recommendations(
    _principal: ViewerDep,
    session: SessionDep,
    campaign_id: uuid.UUID | None = None,
    brand_id: uuid.UUID | None = None,
) -> Page[RecommendationRead]:
    items = await recommendation_service.list_recommendations(session, campaign_id, brand_id)
    return Page[RecommendationRead](items=items)
