"""Request-level ``Idempotency-Key`` store (API_PLAN §1.6, DATA_MODEL §6a.2).

Distinct from ``publication_jobs.idempotency_key`` (A3), which stays the DB-level guard
against duplicate posts. Rows live in their own short transactions (never the request's),
so a replayed response is exactly what the first request returned.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.models import IdempotencyKey
from omnigrowth.storage.db import get_sessionmaker

KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
RETENTION = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Replay:
    status: int
    body: Any


def request_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def validate_key(key: str) -> None:
    if not KEY_PATTERN.match(key):
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "Idempotency-Key must be 8-128 characters of [A-Za-z0-9_-]",
            details={"reason": "IDEMPOTENCY_KEY_INVALID"},
        )


async def begin(
    *,
    principal_id: uuid.UUID,
    method: str,
    route: str,
    key: str,
    body_hash: str,
    principal_type: str = "USER",
) -> Replay | uuid.UUID:
    """Claim the key. Returns the stored response to replay, or the new row id to complete."""
    validate_key(key)
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        scope = (
            IdempotencyKey.principal_type == principal_type,
            IdempotencyKey.principal_id == principal_id,
            IdempotencyKey.method == method,
            IdempotencyKey.route == route,
            IdempotencyKey.key == key,
        )
        # An expired key is forgotten (the one hard-deleted class, DATA_MODEL §6a.2).
        await session.execute(delete(IdempotencyKey).where(*scope, IdempotencyKey.expires_at < now))
        row_id = uuid7()
        inserted = await session.execute(
            pg_insert(IdempotencyKey)
            .values(
                id=row_id,
                principal_type=principal_type,
                principal_id=principal_id,
                method=method,
                route=route,
                key=key,
                state="IN_PROGRESS",
                request_hash=body_hash,
                expires_at=now + RETENTION,
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_keys_principal_method_route_key")
            .returning(IdempotencyKey.id)
        )
        if inserted.scalar_one_or_none() is not None:
            await session.commit()
            return row_id
        existing = (await session.execute(select(IdempotencyKey).where(*scope))).scalar_one()
        await session.commit()
    if existing.request_hash != body_hash:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "this Idempotency-Key was already used with a different request",
            details={"reason": "IDEMPOTENCY_KEY_REUSED"},
        )
    if existing.state != "COMPLETED" or existing.response_status is None:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "a request with this Idempotency-Key is still in progress",
            details={"reason": "IDEMPOTENCY_IN_PROGRESS"},
        )
    return Replay(status=existing.response_status, body=existing.response_body)


async def complete(row_id: uuid.UUID, status: int, body: Any) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.id == row_id)
            .values(state="COMPLETED", response_status=status, response_body=body)
        )
        await session.commit()


async def abandon(row_id: uuid.UUID) -> None:
    """The request failed: forget the key so the client can retry with it."""
    async with get_sessionmaker()() as session:
        await session.execute(delete(IdempotencyKey).where(IdempotencyKey.id == row_id))
        await session.commit()


async def purge_expired(now: datetime | None = None) -> int:
    async with get_sessionmaker()() as session:
        result = await session.execute(
            delete(IdempotencyKey).where(IdempotencyKey.expires_at < (now or datetime.now(UTC)))
        )
        await session.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
