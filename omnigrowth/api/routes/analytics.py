"""``/api/v1/analytics`` and ``/api/v1/recommendations`` (§16, §17, §34, §55 steps 14-16)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from omnigrowth.api.deps import EditorDep, JobQueueDep, OwnerDep, SessionDep, ViewerDep
from omnigrowth.api.idempotency import Outcome, idempotent
from omnigrowth.api.routes.jobs import to_read
from omnigrowth.core.enums import MetricSource, Platform
from omnigrowth.schemas.analytics import (
    AnalyticsPullRequest,
    AnalyticsSnapshotBatchCreate,
    AnalyticsSnapshotIngestResultRead,
    AnalyticsSnapshotRead,
    AttributeComparisonRead,
    CampaignReportRead,
    DerivedMetricDefinitionCreate,
    DerivedMetricDefinitionRead,
    DerivedMetricDefinitionUpdate,
    PublicationMetricsRead,
    RecommendationGenerateRequest,
    RecommendationRead,
)
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.sources import JobRead
from omnigrowth.services import analytics as analytics_service
from omnigrowth.services import jobs as job_service
from omnigrowth.services import metric_definitions
from omnigrowth.services import recommendations as recommendation_service

router = APIRouter(prefix="/analytics", tags=["analytics"])
SourcesQuery = Annotated[
    list[MetricSource] | None,
    Query(description="Only snapshots from these sources (MOCK/FIXTURE are simulated)."),
]
MinSampleQuery = Annotated[int, Query(ge=1, le=10_000)]
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
    request: Request,
    body: AnalyticsSnapshotBatchCreate,
    principal: EditorDep,
    session: SessionDep,
) -> JSONResponse:
    """Manual/fixture ingest [IK] (API_PLAN §2.10)."""

    async def run() -> Outcome:
        result = await analytics_service.ingest_snapshots(session, principal.actor, body)
        return 201, result, {}

    return await idempotent(request, principal, body, run)


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
    campaign_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    since: datetime | None = None,
    until: datetime | None = None,
    sources: SourcesQuery = None,
    min_sample: MinSampleQuery = analytics_service.DEFAULT_MIN_SAMPLE,
) -> CampaignReportRead:
    return await analytics_service.campaign_report(
        session,
        campaign_id,
        since=since,
        until=until,
        sources=sources or (),
        min_sample=min_sample,
    )


@router.get("/publications/{publication_id}/metrics", response_model=PublicationMetricsRead)
async def publication_metrics(
    publication_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    definition_id: uuid.UUID | None = None,
) -> PublicationMetricsRead:
    return await analytics_service.publication_metrics(session, publication_id, definition_id)


@router.get("/attribute-comparisons", response_model=AttributeComparisonRead)
async def attribute_comparisons(
    _principal: ViewerDep,
    session: SessionDep,
    brand_id: uuid.UUID,
    attribute: str,
    metric: str,
    platform: Annotated[list[Platform] | None, Query()] = None,
    campaign_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    sources: SourcesQuery = None,
    min_sample: MinSampleQuery = analytics_service.DEFAULT_MIN_SAMPLE,
) -> AttributeComparisonRead:
    """§16: one attribute against one metric. Correlational, not causal."""
    return await analytics_service.attribute_comparisons(
        session,
        brand_id=brand_id,
        attribute=attribute,
        metric=metric,
        platforms=platform or (),
        campaign_id=campaign_id,
        since=since,
        until=until,
        sources=sources or (),
        min_sample=min_sample,
    )


@router.get("/derived-metric-definitions", response_model=Page[DerivedMetricDefinitionRead])
async def list_definitions(
    _principal: ViewerDep,
    session: SessionDep,
    brand_id: uuid.UUID | None = None,
    include_history: bool = False,
) -> Page[DerivedMetricDefinitionRead]:
    items = await metric_definitions.list_definitions(
        session, brand_id, include_history=include_history
    )
    return Page[DerivedMetricDefinitionRead](items=items)


@router.post(
    "/derived-metric-definitions",
    response_model=DerivedMetricDefinitionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_definition(
    body: DerivedMetricDefinitionCreate, principal: OwnerDep, session: SessionDep
) -> DerivedMetricDefinitionRead:
    return await metric_definitions.create_definition(session, principal.actor, body)


@router.patch(
    "/derived-metric-definitions/{definition_id}", response_model=DerivedMetricDefinitionRead
)
async def revise_definition(
    definition_id: uuid.UUID,
    body: DerivedMetricDefinitionUpdate,
    principal: OwnerDep,
    session: SessionDep,
) -> DerivedMetricDefinitionRead:
    """Any change creates a new revision; reports cite the revision they used."""
    return await metric_definitions.revise_definition(session, principal.actor, definition_id, body)


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
