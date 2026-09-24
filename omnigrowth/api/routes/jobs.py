"""``GET /api/v1/jobs/{job_id}`` — durable async job status (API_PLAN jobs group)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from omnigrowth.api.deps import SessionDep, ViewerDep
from omnigrowth.schemas.sources import JobRead
from omnigrowth.services import jobs as job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


def to_read(view: job_service.JobView) -> JobRead:
    return JobRead(
        id=view.id,
        kind=view.kind,
        status=view.status,
        subject_type=view.subject_type,
        subject_id=view.subject_id,
        result=view.result,
        error_code=view.error_code,
        error=view.error,
        queued_at=view.queued_at,
        started_at=view.started_at,
        finished_at=view.finished_at,
    )


@router.get("/{job_id}", response_model=JobRead)
async def get_job(job_id: uuid.UUID, _principal: ViewerDep, session: SessionDep) -> JobRead:
    return to_read(await job_service.get_job(session, job_id))
