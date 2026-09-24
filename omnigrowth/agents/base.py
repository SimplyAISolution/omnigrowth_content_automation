"""``run_agent`` — one specialised agent call, persisted as an ``agent_runs`` row (§7, §35).

Lifecycle (AGENT_RUN machine, docs/STATE_MACHINES.md):
1. AR1 ``start``: the row is inserted RUNNING with the pinned ``prompt_version_id`` and a
   PII-free ``input_ref``; committed so the attempt is visible even if the worker dies.
2. ``generate_structured`` runs; every provider call becomes a ``model_runs`` row linked to
   the agent run (DbRunRecorder, same session).
3. AR2 ``succeed`` stores the validated output; AR3 ``fail`` stores the error code. Both are
   committed, then a failure is re-raised for the orchestrator.

Agents never touch the database otherwise: persisting their domain rows is the
orchestrator's job (campaigns/orchestrator.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import (
    ActorType,
    AgentRunStatus,
    ModelRole,
    PrivacyClass,
    RoutingProfile,
)
from omnigrowth.core.errors import OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.logging import bind_context
from omnigrowth.core.state_machines import AGENT_RUN, Actor, apply_transition, create_with_state
from omnigrowth.models import AgentRun, AuditEvent
from omnigrowth.prompts import PromptRegistry
from omnigrowth.providers.router import ModelRouter
from omnigrowth.providers.structured import BudgetGuard, RunContext, generate_structured
from omnigrowth.storage.model_runs import DbRunRecorder
from omnigrowth.storage.prompt_versions import ensure_prompt_version

_AGENT_RUNS = cast(Table, AgentRun.__table__)
_AUDIT = cast(Table, AuditEvent.__table__)
_SYSTEM = Actor(ActorType.SYSTEM, "agent-runner")


@dataclass(frozen=True, slots=True)
class AgentDeps:
    """Process-level collaborators, built once per job (or injected by tests)."""

    router: ModelRouter
    registry: PromptRegistry
    budget: BudgetGuard | None = None


@dataclass(frozen=True, slots=True)
class AgentScope:
    """Where a run belongs; never rendered into a prompt (keeps fixture hashes stable)."""

    brand_id: uuid.UUID
    campaign_id: uuid.UUID | None
    correlation_id: str
    routing_profile: RoutingProfile = RoutingProfile.LOCAL_ONLY
    brand_profile_id: uuid.UUID | None = None
    parent_agent_run_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class AgentSpec[T: BaseModel]:
    name: str
    prompt_ref: str
    schema: type[T]
    role: ModelRole
    # Brand/source material is private by default: never sent to a REMOTE model (§52).
    privacy: PrivacyClass = PrivacyClass.PRIVATE


@dataclass(frozen=True, slots=True)
class AgentOutcome[T: BaseModel]:
    agent_run_id: uuid.UUID
    value: T
    provider: str
    model: str
    prompt_ref: str


async def run_agent[T: BaseModel](
    session: AsyncSession,
    spec: AgentSpec[T],
    variables: dict[str, str],
    *,
    deps: AgentDeps,
    scope: AgentScope,
    input_ref: dict[str, Any] | None = None,
) -> AgentOutcome[T]:
    """Run ``spec`` with ``variables``; commits the agent_run (see module docstring)."""
    prompt = deps.registry.get(spec.prompt_ref)
    rendered = prompt.render(variables)
    prompt_version_id = await ensure_prompt_version(session, prompt)
    run_id = uuid7()
    await create_with_state(
        session,
        machine=AGENT_RUN,
        table=_AGENT_RUNS,
        audit_table=_AUDIT,
        row={
            "id": run_id,
            "brand_id": scope.brand_id,
            "campaign_id": scope.campaign_id,
            "parent_agent_run_id": scope.parent_agent_run_id,
            "agent_name": spec.name,
            "prompt_version_id": prompt_version_id,
            "brand_profile_id": scope.brand_profile_id,
            "input_ref": {
                **(input_ref or {}),
                "prompt": rendered.prompt_ref,
                "input_hash": rendered.input_hash,
            },
            "correlation_id": scope.correlation_id,
        },
        trigger="start",
        actor=Actor(ActorType.AGENT, spec.name),
        correlation_id=scope.correlation_id,
        brand_id=scope.brand_id,
    )
    await session.commit()

    context = RunContext(
        task=spec.name,
        role=spec.role,
        privacy=spec.privacy,
        correlation_id=scope.correlation_id,
        profile=scope.routing_profile,
        brand_id=scope.brand_id,
        campaign_id=scope.campaign_id,
        agent_run_id=run_id,
        prompt_version_id=prompt_version_id,
    )
    with bind_context(agent=spec.name, agent_run_id=str(run_id)):
        try:
            result = await generate_structured(
                spec.schema,
                rendered,
                router=deps.router,
                context=context,
                recorder=DbRunRecorder(session),
                budget=deps.budget,
            )
        except OmniGrowthError as exc:
            await _finish(session, run_id, scope, "fail", {"error_code": exc.code})
            raise
    await _finish(
        session, run_id, scope, "succeed", {"output": result.value.model_dump(mode="json")}
    )
    return AgentOutcome(
        agent_run_id=run_id,
        value=result.value,
        provider=result.model.provider_name,
        model=result.model.model,
        prompt_ref=rendered.prompt_ref,
    )


async def _finish(
    session: AsyncSession,
    run_id: uuid.UUID,
    scope: AgentScope,
    trigger: str,
    values: dict[str, Any],
) -> None:
    await apply_transition(
        session,
        machine=AGENT_RUN,
        table=_AGENT_RUNS,
        audit_table=_AUDIT,
        entity_id=run_id,
        from_state=AgentRunStatus.RUNNING,
        trigger=trigger,
        actor=_SYSTEM,
        correlation_id=scope.correlation_id,
        brand_id=scope.brand_id,
        values={**values, "finished_at": datetime.now(UTC)},
    )
    await session.commit()
