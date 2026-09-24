"""Persist the prompt registry into ``prompt_versions`` (DATA_MODEL §6.1).

Prompt versions are immutable: a file whose content changed without a version bump is a
CONFLICT. Functions here never commit; the caller owns the transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.models import PromptVersion
from omnigrowth.prompts.registry import Prompt, PromptRegistry


def _changed(prompt: Prompt, stored_hash: str) -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.CONFLICT,
        f"prompt {prompt.ref} changed after release; bump the version",
        details={"ref": prompt.ref, "stored_hash": stored_hash, "file_hash": prompt.content_hash},
    )


def _db_failure(action: str, exc: SQLAlchemyError) -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.DATABASE_FAILURE,
        f"prompt_versions {action} failed",
        details={"error": type(exc).__name__},
        retryable=True,
    )


async def get_prompt_version_id(session: AsyncSession, prompt: Prompt) -> uuid.UUID | None:
    """Id of the stored row for ``prompt``; None if absent; CONFLICT if its hash differs."""
    stmt = select(PromptVersion.id, PromptVersion.content_hash).where(
        PromptVersion.domain == prompt.domain,
        PromptVersion.name == prompt.name,
        PromptVersion.version == prompt.version,
    )
    try:
        row = (await session.execute(stmt)).one_or_none()
    except SQLAlchemyError as exc:
        raise _db_failure("lookup", exc) from exc
    if row is None:
        return None
    if row.content_hash != prompt.content_hash:
        raise _changed(prompt, row.content_hash)
    found: uuid.UUID = row.id
    return found


async def ensure_prompt_version(session: AsyncSession, prompt: Prompt) -> uuid.UUID:
    """Id of ``prompt``'s stored row, inserting it first if absent (CONFLICT if changed)."""
    existing = await get_prompt_version_id(session, prompt)
    if existing is not None:
        return existing
    row = PromptVersion(
        id=uuid7(),
        domain=prompt.domain,
        name=prompt.name,
        version=prompt.version,
        content_hash=prompt.content_hash,
        front_matter=prompt.front_matter.model_dump(mode="json"),
        body=prompt.text,
    )
    session.add(row)
    try:
        await session.flush([row])
    except IntegrityError as exc:
        # Lost a race with a concurrent loader inserting the same (domain, name, version).
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            f"prompt {prompt.ref} was inserted concurrently; retry the sync",
            details={"ref": prompt.ref},
            retryable=True,
        ) from exc
    except SQLAlchemyError as exc:
        raise _db_failure("insert", exc) from exc
    return row.id


async def sync_prompt_versions(
    session: AsyncSession, registry: PromptRegistry
) -> dict[str, uuid.UUID]:
    """Insert missing prompt versions, verify existing ones; return ``ref -> id``."""
    return {prompt.ref: await ensure_prompt_version(session, prompt) for prompt in registry}
