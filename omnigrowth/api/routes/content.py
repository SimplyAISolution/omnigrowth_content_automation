"""``/api/v1/content`` — assets, versions (§36), diffs, QA results (§11), similarity (§10)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from omnigrowth.api.deps import EditorDep, JobQueueDep, SessionDep, ViewerDep
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
    SimilarContentRead,
)
from omnigrowth.services import content as content_service

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
    items = await content_service.similar(
        session, asset_id, threshold if threshold is not None else config.similarity.near_duplicate
    )
    return Page[SimilarContentRead](items=items)
