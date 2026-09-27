"""``/api/v1/content`` — assets, versions (§36), diffs, QA results (§11), similarity (§10)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import JSONResponse

from omnigrowth.api.deps import EditorDep, JobQueueDep, SessionDep, ViewerDep
from omnigrowth.api.idempotency import Outcome, idempotent
from omnigrowth.api.routes.jobs import to_read
from omnigrowth.content.qa import cached_qa_config
from omnigrowth.core.enums import ContentAssetStatus
from omnigrowth.core.settings import get_settings
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.content import (
    ContentAssetRead,
    ContentVersionCreate,
    ContentVersionDiffRead,
    ContentVersionRead,
    QaResultRead,
    RegenerateRequest,
    RepurposeRequest,
    SimilarContentRead,
)
from omnigrowth.schemas.sources import JobRead
from omnigrowth.services import content as content_service
from omnigrowth.services import jobs as job_service

router = APIRouter(prefix="/content", tags=["content"])


@router.get("", response_model=Page[ContentAssetRead])
async def list_content(
    _principal: ViewerDep,
    session: SessionDep,
    brand_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    status_filter: Annotated[list[ContentAssetStatus] | None, Query(alias="status")] = None,
) -> Page[ContentAssetRead]:
    items = await content_service.list_assets(
        session, brand_id=brand_id, campaign_id=campaign_id, statuses=status_filter or ()
    )
    return Page[ContentAssetRead](items=items)


@router.get("/{asset_id}", response_model=ContentAssetRead)
async def get_content(
    asset_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> ContentAssetRead:
    return await content_service.get_asset(session, asset_id)


@router.get("/{asset_id}/versions", response_model=Page[ContentVersionRead])
async def list_versions(
    asset_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> Page[ContentVersionRead]:
    return Page[ContentVersionRead](items=await content_service.list_versions(session, asset_id))


@router.post(
    "/{asset_id}/versions", response_model=ContentVersionRead, status_code=status.HTTP_201_CREATED
)
async def create_version(
    asset_id: uuid.UUID,
    body: ContentVersionCreate,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
    response: Response,
) -> ContentVersionRead:
    """Human edit (§36/§37 EDIT): new version, asset back to GENERATED, QA re-run job."""
    version, job_id = await content_service.edit_asset(
        session, queue, principal.actor, asset_id, body
    )
    response.headers["X-Job-ID"] = str(job_id)
    return version


@router.get("/{asset_id}/versions/{version_id}", response_model=ContentVersionRead)
async def get_version(
    asset_id: uuid.UUID, version_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> ContentVersionRead:
    return await content_service.get_version(session, asset_id, version_id)


@router.get("/{asset_id}/diff", response_model=ContentVersionDiffRead)
async def diff_versions(
    asset_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    from_version_id: uuid.UUID | None = None,
    to_version_id: uuid.UUID | None = None,
) -> ContentVersionDiffRead:
    return await content_service.diff(session, asset_id, from_version_id, to_version_id)


@router.get("/{asset_id}/qa-results", response_model=Page[QaResultRead])
async def list_qa_results(
    asset_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    version_id: uuid.UUID | None = None,
) -> Page[QaResultRead]:
    items = await content_service.qa_results(session, asset_id, version_id)
    return Page[QaResultRead](items=items)


@router.get("/{asset_id}/similar", response_model=Page[SimilarContentRead])
async def similar_content(
    asset_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    threshold: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
) -> Page[SimilarContentRead]:
    """§10 duplicate/fatigue neighbours (warn-only). Default threshold from config/qa.yaml."""
    config = cached_qa_config(get_settings().config_dir)
    items = await content_service.similar(session, asset_id, threshold, config)
    return Page[SimilarContentRead](items=items)


@router.post("/{asset_id}/regenerate", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def regenerate(
    request: Request,
    asset_id: uuid.UUID,
    body: RegenerateRequest,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> JSONResponse:
    """§37 REGENERATE: back to DRAFT (CA18/CA19); the writer produces a REGENERATION version
    from ``instructions`` and QA re-runs. Same service as the approval action."""

    async def run() -> Outcome:
        job_id = await content_service.regenerate_asset(
            session, queue, principal.actor, asset_id, body.content_version_id, body.instructions
        )
        job = to_read(await job_service.get_job(session, job_id))
        return 202, job, {"Location": f"/api/v1/jobs/{job_id}"}

    return await idempotent(request, principal, {"asset_id": asset_id, **body.model_dump()}, run)


@router.get("/{asset_id}/derivatives", response_model=Page[ContentAssetRead])
async def list_derivatives(
    asset_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> Page[ContentAssetRead]:
    """Assets repurposed directly from this one (repurposing graph, §9)."""
    return Page[ContentAssetRead](items=await content_service.list_derivatives(session, asset_id))


@router.post("/{asset_id}/repurpose", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def repurpose(
    request: Request,
    asset_id: uuid.UUID,
    body: RepurposeRequest,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> JSONResponse:
    """§9: a DRAFT derivative for another platform/format, written from this asset's current
    version by an ASSET_GENERATION job. 409 on a recursive duplicate, a duplicate
    derivative or a chain deeper than 5."""

    async def run() -> Outcome:
        job_id = await content_service.repurpose_asset(
            session, queue, principal.actor, asset_id, body
        )
        job = to_read(await job_service.get_job(session, job_id))
        return 202, job, {"Location": f"/api/v1/jobs/{job_id}"}

    return await idempotent(request, principal, {"asset_id": asset_id, **body.model_dump()}, run)


@router.post("/{asset_id}/qa", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def rerun_qa(
    request: Request,
    asset_id: uuid.UUID,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> JSONResponse:
    """Re-run QA stages 1-4 on the current version of a GENERATED asset (e.g. after an
    infrastructure failure left it without results)."""

    async def run() -> Outcome:
        job_id = await content_service.request_qa(session, queue, principal.actor, asset_id)
        return 202, to_read(await job_service.get_job(session, job_id)), {}

    return await idempotent(request, principal, {"asset_id": asset_id}, run)
