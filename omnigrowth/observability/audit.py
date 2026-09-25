"""Audit timeline writer for non-status changes (spec §28 audit timeline, §37 "record who").

Status transitions are audited by ``core.state_machines.apply``; everything else (creating a
brand, editing a profile, archiving an offer, credential rotation ...) goes through
``record_audit`` in the same transaction as the change. ``audit_events`` is append-only.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import Table, func, insert
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.ids import new_correlation_id, uuid7
from omnigrowth.core.logging import get_context
from omnigrowth.core.masking import mask_mapping
from omnigrowth.core.state_machines import Actor
from omnigrowth.models import AuditEvent

_AUDIT = cast(Table, AuditEvent.__table__)


async def record_audit(
    session: AsyncSession,
    *,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    brand_id: uuid.UUID | None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> uuid.UUID:
    """Append one audit row. ``entity_type`` is the table name (DATA_MODEL §3.9).

    Values are JSON-serialisable snapshots of the changed fields; they are masked here so
    secrets can never reach the audit log.
    """
    event_id = uuid7()
    correlation_id = get_context().get("correlation_id") or new_correlation_id()
    await session.execute(
        insert(_AUDIT).values(
            id=event_id,
            occurred_at=func.clock_timestamp(),
            correlation_id=correlation_id,
            actor_type=actor.type,
            actor_id=actor.id,
            brand_id=brand_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before=mask_mapping(before) if before is not None else None,
            after=mask_mapping(after) if after is not None else None,
            **{"metadata": mask_mapping(metadata or {})},
        )
    )
    return event_id
