"""The single code path that writes ``status`` columns (AGENTS.md §4).

``apply_transition`` performs an optimistic compare-and-set
``UPDATE <table> SET status=:to, ... WHERE id=:id AND status=:from`` and writes the
``audit_events`` row in the same transaction. Tables are passed in (SQLAlchemy ``Table``)
so ``core`` stays independent of ``omnigrowth.models`` (import-linter contract).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import ColumnElement, Table, func, insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.masking import mask_mapping
from omnigrowth.core.state_machines.machine import (
    Actor,
    StateMachine,
    Transition,
    audit_payload,
)


def build_audit_row(
    *,
    table_name: str,
    entity_id: uuid.UUID,
    transition: Transition,
    from_state: str | None,
    actor: Actor,
    correlation_id: str,
    brand_id: uuid.UUID | None,
    reason: str | None,
    extra_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = {**audit_payload(transition, from_state, reason), **(extra_metadata or {})}
    return {
        "id": uuid7(),
        "correlation_id": correlation_id,
        "actor_type": actor.type.value,
        "actor_id": actor.id,
        "brand_id": brand_id,
        "entity_type": table_name,
        "entity_id": entity_id,
        "action": "status.transition",
        "before": None if from_state is None else {"status": from_state},
        "after": {"status": transition.target},
        "metadata": mask_mapping(metadata),
    }


async def apply_transition(
    session: AsyncSession,
    *,
    machine: StateMachine,
    table: Table,
    audit_table: Table,
    entity_id: uuid.UUID,
    from_state: str,
    trigger: str,
    actor: Actor,
    correlation_id: str,
    brand_id: uuid.UUID | None,
    reason: str | None = None,
    values: Mapping[str, Any] | None = None,
    fence: Mapping[str, Any] | None = None,
    extra_metadata: Mapping[str, Any] | None = None,
    conditions: Sequence[ColumnElement[bool]] = (),
) -> Transition:
    """Apply ``trigger`` to an existing row. Guards must have been checked by the caller.

    ``values``: extra columns to set in the same UPDATE (e.g. ``approved_version_id``).
    ``fence``: extra equality predicates (e.g. ``{"claim_token": t}`` for job outcomes).
    ``conditions``: extra SQL predicates evaluated by the database in the same UPDATE (e.g.
    ``lease_expires_at > now() + timeout`` at dispatch start, STATE_MACHINES §3.4).
    Raises INVALID_STATE_TRANSITION / FORBIDDEN (structural) or CONFLICT (lost race).
    """
    transition = machine.resolve(from_state, trigger, actor)
    if values and "status" in values:
        raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "status is set by the machine")

    stmt = (
        update(table)
        .where(table.c.id == entity_id, table.c.status == from_state)
        .values(status=transition.target, **dict(values or {}))
    )
    if "updated_at" in table.c:
        stmt = stmt.values(updated_at=func.now())
    for column, expected in (fence or {}).items():
        stmt = stmt.where(table.c[column] == expected)
    for condition in conditions:
        stmt = stmt.where(condition)

    result = await session.execute(stmt)
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            f"{machine.name} {entity_id} is no longer in state {from_state}",
            details={"transition": transition.id, "expected": from_state},
        )

    await session.execute(
        insert(audit_table).values(
            # clock_timestamp(): transitions within one transaction keep their order.
            occurred_at=func.clock_timestamp(),
            **build_audit_row(
                table_name=table.name,
                entity_id=entity_id,
                transition=transition,
                from_state=from_state,
                actor=actor,
                correlation_id=correlation_id,
                brand_id=brand_id,
                reason=reason,
                extra_metadata=extra_metadata,
            ),
        )
    )
    return transition


async def create_with_state(
    session: AsyncSession,
    *,
    machine: StateMachine,
    table: Table,
    audit_table: Table,
    row: Mapping[str, Any],
    trigger: str,
    actor: Actor,
    correlation_id: str,
    brand_id: uuid.UUID | None,
    reason: str | None = None,
) -> Transition:
    """Insert a new row in the machine's initial state (creation transitions C1, CA1, J1…)."""
    transition = machine.initial(trigger, actor)
    if "status" in row:
        raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "status is set by the machine")
    entity_id = row.get("id") or uuid7()
    await session.execute(
        insert(table).values(**{**row, "id": entity_id, "status": transition.target})
    )
    await session.execute(
        insert(audit_table).values(
            # clock_timestamp(): transitions within one transaction keep their order.
            occurred_at=func.clock_timestamp(),
            **build_audit_row(
                table_name=table.name,
                entity_id=entity_id,
                transition=transition,
                from_state=None,
                actor=actor,
                correlation_id=correlation_id,
                brand_id=brand_id,
                reason=reason,
            ),
        )
    )
    return transition
