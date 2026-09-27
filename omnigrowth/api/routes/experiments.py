"""``/api/v1/experiments`` (spec §18, API_PLAN §2.11, SPEC_AMENDMENTS A14)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from omnigrowth.api.deps import EditorDep, JobQueueDep, SessionDep, ViewerDep
from omnigrowth.api.idempotency import Outcome, idempotent
from omnigrowth.api.routes.jobs import to_read
from omnigrowth.core.enums import ExperimentStatus
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.experiments import (
    ExperimentCreate,
    ExperimentRead,
    ExperimentResultRead,
    UnitRead,
    UnitsCreate,
)
from omnigrowth.schemas.sources import JobRead
from omnigrowth.services import experiments as experiment_service
from omnigrowth.services import jobs as job_service

router = APIRouter(prefix="/experiments", tags=["experiments"])


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


@router.get("", response_model=Page[ExperimentRead])
async def list_experiments(
    _principal: ViewerDep,
    session: SessionDep,
    brand_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    status: ExperimentStatus | None = None,
) -> Page[ExperimentRead]:
    items = await experiment_service.list_experiments(
        session, brand_id=brand_id, campaign_id=campaign_id, status=status
    )
    return Page[ExperimentRead](items=items)


@router.post("", response_model=ExperimentRead, status_code=status.HTTP_201_CREATED)
async def create_experiment(
    body: ExperimentCreate, principal: EditorDep, session: SessionDep
) -> ExperimentRead:
    """E1: a pre-registered design (metric, unit, age, allocation, stopping rule, minimum
    effect, power) and variants; the first variant is the control."""
    return await experiment_service.create_experiment(session, principal.actor, body)


@router.get("/{experiment_id}", response_model=ExperimentRead)
async def get_experiment(
    experiment_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> ExperimentRead:
    return await experiment_service.get_experiment(session, experiment_id)


@router.post(
    "/{experiment_id}/units", response_model=list[UnitRead], status_code=status.HTTP_201_CREATED
)
async def register_units(
    experiment_id: uuid.UUID, body: UnitsCreate, principal: EditorDep, session: SessionDep
) -> list[UnitRead]:
    """Randomized: the server allocates each post to a variant (blocked by account).
    Observational: the caller labels each post; such experiments never declare a winner."""
    return await experiment_service.register_units(session, principal.actor, experiment_id, body)


@router.post("/{experiment_id}/start", response_model=ExperimentRead)
async def start_experiment(
    experiment_id: uuid.UUID, principal: EditorDep, session: SessionDep
) -> ExperimentRead:
    """E2: guards, then the sample plan is computed and frozen with the thresholds."""
    return await experiment_service.start_experiment(
        session, principal.actor, experiment_id, now=datetime.now(UTC)
    )


@router.post("/{experiment_id}/cancel", response_model=ExperimentRead)
async def cancel_experiment(
    experiment_id: uuid.UUID, body: CancelRequest, principal: EditorDep, session: SessionDep
) -> ExperimentRead:
    return await experiment_service.cancel_experiment(
        session, principal.actor, experiment_id, body.reason
    )


@router.post(
    "/{experiment_id}/evaluate", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED
)
async def evaluate_experiment(
    request: Request,
    experiment_id: uuid.UUID,
    principal: EditorDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> JSONResponse:
    """[IK] Queues the single fixed-endpoint evaluation. Before ``end_at`` the job finishes
    with ``result.no_transition = WINDOW_OPEN`` (no peeking)."""

    async def run() -> Outcome:
        job_id = await experiment_service.request_evaluation(
            session, queue, principal.actor, experiment_id
        )
        return 202, to_read(await job_service.get_job(session, job_id)), {}

    return await idempotent(request, principal, {"experiment_id": str(experiment_id)}, run)


@router.get("/{experiment_id}/results", response_model=ExperimentResultRead)
async def get_results(
    experiment_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> ExperimentResultRead:
    return await experiment_service.get_results(session, experiment_id)
