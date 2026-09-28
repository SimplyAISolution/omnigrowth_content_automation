"""``/api/v1/publications`` — outbox jobs, external posts and reconciliation (§14, A3)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from omnigrowth.api.deps import EditorDep, SessionDep, ViewerDep
from omnigrowth.api.idempotency import Outcome, idempotent
from omnigrowth.core.enums import PublicationJobStatus, PublicationStatus
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.publishing import (
    CancelRequest,
    PublicationJobRead,
    PublicationLookupRead,
    PublicationPreviewRead,
    PublicationRead,
    PublicationReconcileRequest,
)
from omnigrowth.services import connectors
from omnigrowth.services import publishing as publishing_service

router = APIRouter(prefix="/publications", tags=["publications"])


@router.get("/jobs", response_model=Page[PublicationJobRead])
async def list_jobs(
    _principal: ViewerDep,
    session: SessionDep,
    campaign_id: uuid.UUID | None = None,
    status: Annotated[list[PublicationJobStatus] | None, Query()] = None,
) -> Page[PublicationJobRead]:
    items = await publishing_service.list_jobs(
        session, campaign_id=campaign_id, statuses=status or []
    )
    return Page[PublicationJobRead](items=items)


@router.get("/jobs/{job_id}", response_model=PublicationJobRead)
async def get_job(
    job_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> PublicationJobRead:
    return await publishing_service.get_job(session, job_id)


@router.get("/jobs/{job_id}/preview", response_model=PublicationPreviewRead)
async def preview_job(
    job_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> PublicationPreviewRead:
    """Exactly what would be sent (account, text, media, visibility, timing, evidence class)
    and what would block it. Read-only; decrypts nothing."""
    return await connectors.preview_job(session, job_id)


@router.post("/jobs/{job_id}/cancel", response_model=PublicationJobRead)
async def cancel_job(
    job_id: uuid.UUID, body: CancelRequest, principal: EditorDep, session: SessionDep
) -> PublicationJobRead:
    return await publishing_service.cancel_job(session, principal.actor, job_id, body.reason)


@router.get("", response_model=Page[PublicationRead])
async def list_publications(
    _principal: ViewerDep,
    session: SessionDep,
    campaign_id: uuid.UUID | None = None,
    status: Annotated[list[PublicationStatus] | None, Query()] = None,
) -> Page[PublicationRead]:
    items = await publishing_service.list_publications(
        session, campaign_id=campaign_id, statuses=status or []
    )
    return Page[PublicationRead](items=items)


@router.get("/{publication_id}", response_model=PublicationRead)
async def get_publication(
    publication_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> PublicationRead:
    return await publishing_service.get_publication(session, publication_id)


@router.post("/{publication_id}/reconcile", response_model=PublicationRead)
async def reconcile(
    request: Request,
    publication_id: uuid.UUID,
    body: PublicationReconcileRequest,
    principal: EditorDep,
    session: SessionDep,
) -> JSONResponse:
    """Resolve PUBLICATION_UNCERTAIN: confirm published (EDITOR) or not published (OWNER)."""

    async def run() -> Outcome:
        result = await publishing_service.reconcile(session, principal.actor, publication_id, body)
        return 200, result, {}

    inputs = {"publication_id": publication_id, **body.model_dump()}
    return await idempotent(request, principal, inputs, run)


@router.post("/{publication_id}/lookup", response_model=PublicationLookupRead)
async def lookup(
    publication_id: uuid.UUID, principal: EditorDep, session: SessionDep
) -> PublicationLookupRead:
    """Search the platform for an uncertain LIVE post by its exact text (never re-posts).
    FOUND reconciles it; PARTIAL / NOT_FOUND leave the decision to a person."""
    return await connectors.lookup_publication(session, principal.actor, publication_id)
