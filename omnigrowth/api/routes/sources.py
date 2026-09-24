"""``/api/v1/sources`` — paste text (sync), upload file / ingest URL (async job), read, archive."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from omnigrowth.api.deps import (
    EditorDep,
    IngestRateLimit,
    JobQueueDep,
    SessionDep,
    UploadStoreDep,
    ViewerDep,
)
from omnigrowth.api.routes.jobs import to_read
from omnigrowth.core.enums import SourceType
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.ingestion import DEFAULT_LIMITS
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.sources import (
    JobRead,
    SourceAssetRead,
    SourceContentRead,
    SourceTextCreate,
    SourceUrlIngestRequest,
)
from omnigrowth.services import jobs as job_service
from omnigrowth.services import sources as source_service

router = APIRouter(prefix="/sources", tags=["sources"])


def _read(view: source_service.SourceView) -> SourceAssetRead:
    return SourceAssetRead(**dataclasses.asdict(view))


async def _job_read(session: SessionDep, job_id: uuid.UUID) -> JobRead:
    return to_read(await job_service.get_job(session, job_id))


@router.post(
    "/text",
    response_model=SourceAssetRead,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": SourceAssetRead, "description": "Duplicate of an existing source"}},
)
async def create_text_source(
    body: SourceTextCreate, principal: EditorDep, session: SessionDep, response: Response
) -> SourceAssetRead:
    accepted = await source_service.ingest_text(
        session,
        actor=principal.actor,
        brand_id=body.brand_id,
        source_type=body.source_type,
        content=body.content,
        title=body.title,
    )
    if accepted.duplicate:
        response.status_code = status.HTTP_200_OK
        response.headers["X-Duplicate-Of"] = str(accepted.source.id)
    return _read(accepted.source)


@router.post(
    "/upload",
    dependencies=[IngestRateLimit],
    response_model=JobRead | SourceAssetRead,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"model": SourceAssetRead, "description": "Duplicate of an existing source"}},
)
async def upload_source(
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
    store: UploadStoreDep,
    response: Response,
    file: Annotated[UploadFile, File()],
    brand_id: Annotated[uuid.UUID, Form()],
    title: Annotated[str | None, Form(max_length=500)] = None,
) -> JobRead | SourceAssetRead:
    # Read at most limit+1 bytes: never buffer an arbitrarily large body.
    data = await file.read(DEFAULT_LIMITS.max_bytes + 1)
    if len(data) > DEFAULT_LIMITS.max_bytes:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE, "upload too large", details={"limit": "25 MiB"}
        )
    accepted = await source_service.accept_upload(
        session,
        actor=principal.actor,
        store=store,
        queue=queue,
        brand_id=brand_id,
        data=data,
        filename=file.filename,
        declared_content_type=file.content_type,
        title=title,
    )
    if accepted.duplicate or accepted.job_id is None:
        response.status_code = status.HTTP_200_OK
        response.headers["X-Duplicate-Of"] = str(accepted.source.id)
        return _read(accepted.source)
    response.headers["Location"] = f"/api/v1/jobs/{accepted.job_id}"
    return await _job_read(session, accepted.job_id)


@router.post(
    "/url",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[IngestRateLimit],
)
async def ingest_url(
    body: SourceUrlIngestRequest,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
    response: Response,
) -> JobRead:
    accepted = await source_service.accept_url(
        session,
        actor=principal.actor,
        queue=queue,
        brand_id=body.brand_id,
        url=body.url,
        source_type=body.source_type,
        title=body.title,
    )
    if accepted.job_id is None:  # pragma: no cover - accept_url always enqueues
        raise OmniGrowthError(ErrorCode.QUEUE_FAILURE, "job was not created")
    response.headers["Location"] = f"/api/v1/jobs/{accepted.job_id}"
    return await _job_read(session, accepted.job_id)


@router.get("", response_model=Page[SourceAssetRead])
async def list_sources(
    _principal: ViewerDep,
    session: SessionDep,
    brand_id: uuid.UUID | None = None,
    source_type: SourceType | None = None,
    cursor: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    include_archived: bool = False,
) -> Page[SourceAssetRead]:
    items, next_cursor = await source_service.list_sources(
        session,
        brand_id=brand_id,
        source_type=source_type,
        cursor=cursor,
        limit=limit,
        include_archived=include_archived,
    )
    return Page[SourceAssetRead](
        items=[_read(i) for i in items], next_cursor=str(next_cursor) if next_cursor else None
    )


@router.get("/{source_id}", response_model=SourceAssetRead)
async def get_source(
    source_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> SourceAssetRead:
    return _read(await source_service.get_source(session, source_id))


@router.get("/{source_id}/content", response_model=SourceContentRead)
async def get_source_content(
    source_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    part: Annotated[str, Query(pattern="^(raw|normalized|transcript)$")] = "normalized",
) -> SourceContentRead:
    _view, content = await source_service.get_source_content(session, source_id, part)
    return SourceContentRead(source_id=source_id, part=part, content=content)


@router.post("/{source_id}/archive", response_model=SourceAssetRead)
async def archive_source(
    source_id: uuid.UUID, principal: EditorDep, session: SessionDep
) -> SourceAssetRead:
    return _read(await source_service.archive_source(session, source_id, principal.actor))


@router.post(
    "/{source_id}/retry",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[IngestRateLimit],
)
async def retry_source(
    source_id: uuid.UUID,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
    response: Response,
) -> JobRead:
    """FAILED -> PENDING + new ingest job (transient failures must be recoverable)."""
    accepted = await source_service.retry_source(
        session, actor=principal.actor, queue=queue, source_id=source_id
    )
    if accepted.job_id is None:  # pragma: no cover - retry always enqueues
        raise OmniGrowthError(ErrorCode.QUEUE_FAILURE, "job was not created")
    response.headers["Location"] = f"/api/v1/jobs/{accepted.job_id}"
    return await _job_read(session, accepted.job_id)
