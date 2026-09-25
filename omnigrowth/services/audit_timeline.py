"""Audit timeline reads (API_PLAN ``/audit/timeline``): ascending, masked metadata."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.models import AuditEvent

# Tables whose history VIEWERs may read (users / api_tokens are OWNER-only elsewhere).
ENTITY_TYPES = frozenset(
    {
        "brands",
        "brand_profiles",
        "campaigns",
        "content_assets",
        "content_versions",
        "content_ideas",
        "publication_jobs",
        "publications",
        "experiments",
        "platform_accounts",
        "recommendations",
        "source_assets",
        "jobs",
        "agent_runs",
    }
)


async def timeline(
    session: AsyncSession, entity_type: str, entity_id: uuid.UUID, limit: int
) -> list[dict[str, Any]]:
    if entity_type not in ENTITY_TYPES:
        raise OmniGrowthError(
            ErrorCode.NOT_FOUND, "unknown entity type", details={"entity_type": entity_type}
        )
    rows = (
        await session.execute(
            select(AuditEvent)
            .where(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == entity_id)
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
            .limit(limit)
        )
    ).scalars()
    return [
        {
            "id": str(e.id),
            "occurred_at": e.occurred_at.isoformat(),
            "correlation_id": e.correlation_id,
            "actor_type": e.actor_type.value,
            "actor_id": e.actor_id,
            "entity_type": e.entity_type,
            "entity_id": str(e.entity_id),
            "action": e.action,
            "metadata": e.metadata_,
        }
        for e in rows
    ]
