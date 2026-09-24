"""Database ``RunRecorder``: one immutable ``model_runs`` row per provider call.

Spec §51 cost tracking, DATA_MODEL §6.3.

``model_runs`` is an append-only log, not a state machine: its ``status`` is the outcome of a
finished call, written once at insert. This file is therefore the one documented exception
to the "status is written only by core/state_machines" rule (AGENTS.md §4 scope note in
GLOSSARY §4a); rows are never updated.
"""

from __future__ import annotations

import uuid

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.ids import uuid7
from omnigrowth.models import ModelRun
from omnigrowth.providers.structured import ModelRunRecord

_RAW_LIMIT = 20_000


class DbRunRecorder:
    """Inserts into the caller's session; the caller owns commit/rollback."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, record: ModelRunRecord) -> uuid.UUID:
        run_id = uuid7()
        ctx = record.context
        row = {
            "id": run_id,
            "brand_id": ctx.brand_id,
            "campaign_id": ctx.campaign_id,
            "agent_run_id": ctx.agent_run_id,
            "repair_of_model_run_id": record.repair_of_model_run_id,
            "attempt_no": record.attempt_no,
            "task": ctx.task,
            "model_role": ctx.role,
            "routing_profile": ctx.profile,
            "provider": record.model.provider_name,
            "model": record.model.model,
            "locality": record.model.locality,
            "privacy_class": ctx.privacy,
            "structured_output_mode": (
                str(record.structured_output_mode) if record.structured_output_mode else None
            ),
            "prompt_version_id": ctx.prompt_version_id,
            "input_hash": record.input_hash,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "estimated_cost_usd": record.estimated_cost_usd,
            "latency_ms": record.latency_ms,
            "status": record.status,
            "error_code": record.error_code,
            # Raw payload kept on failure for debugging (AGENTS.md §4), bounded in size.
            "raw_response": (
                record.raw_response[:_RAW_LIMIT] if record.raw_response is not None else None
            ),
            "correlation_id": ctx.correlation_id,
        }
        await self._session.execute(insert(ModelRun).values(**row))
        return run_id
