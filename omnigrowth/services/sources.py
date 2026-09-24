"""Source ingestion use-cases (spec §2, §26, §27; API_PLAN sources group).

- Pasted text: parsed synchronously (cheap) -> NORMALIZED immediately.
- File upload / URL: validated synchronously (size, magic bytes vs declared type, SSRF
  pre-check), stored/recorded as PENDING, then processed by a SOURCE_INGEST job.
- Duplicate content per brand (same sha256 of the original bytes) is never stored twice.
``source_assets.status`` changes only through the SOURCE_ASSET state machine.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import JobKind, SourceAssetStatus, SourceType
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.logging import get_context
from omnigrowth.core.state_machines import SOURCE_ASSET, Actor, apply_transition, create_with_state
from omnigrowth.ingestion import DEFAULT_LIMITS, NormalizedDocument, parse, parse_html, sniff_type
from omnigrowth.models import AuditEvent, Brand, SourceAsset
from omnigrowth.observability.audit import record_audit
from omnigrowth.security.http import FetchResult, precheck_url, safe_fetch
from omnigrowth.services.jobs import (
    JobQueue,
    cancel_queued,
    create_job,
    dispatch,
    system_actor,
)
from omnigrowth.storage.uploads import UploadStore

log = logging.getLogger(__name__)

_SOURCES = cast(Table, SourceAsset.__table__)
_AUDIT = cast(Table, AuditEvent.__table__)

TEXT_SOURCE_TYPES = frozenset(
    {
        SourceType.TEXT,
        SourceType.MARKDOWN,
        SourceType.TRANSCRIPT,
        SourceType.SUBTITLE,
        SourceType.NOTE,
    }
)
MAX_PASTE_BYTES = 1024 * 1024  # API_PLAN: pasted text max 1 MiB
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")

Fetcher = Callable[[str], Awaitable[FetchResult]]


@dataclass(frozen=True, slots=True)
class SourceView:
    id: uuid.UUID
    brand_id: uuid.UUID
    source_type: SourceType
    status: SourceAssetStatus
    title: str | None
    source_url: str | None
    mime_type: str | None
    byte_size: int | None
    checksum: str
    metadata: dict[str, Any]
    has_transcript: bool
    error_code: ErrorCode | None


@dataclass(frozen=True, slots=True)
class AcceptedSource:
    source: SourceView
    duplicate: bool
    job_id: uuid.UUID | None = None


def _view(row: SourceAsset) -> SourceView:
    return SourceView(
        id=row.id,
        brand_id=row.brand_id,
        source_type=SourceType(row.source_type),
        status=SourceAssetStatus(row.status),
        title=row.title,
        source_url=row.source_url,
        mime_type=row.mime_type,
        byte_size=row.byte_size,
        checksum=row.checksum,
        metadata=dict(row.metadata_ or {}),
        has_transcript=row.transcript is not None,
        error_code=ErrorCode(row.error_code) if row.error_code else None,
    )


def _correlation() -> str:
    return get_context().get("correlation_id") or uuid7().hex


def safe_filename(name: str | None) -> str | None:
    if not name:
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _SAFE_FILENAME.sub("_", base).strip(" .")[:200]
    return cleaned or None


# ------------------------------------------------------------------ queries
async def _require_brand(session: AsyncSession, brand_id: uuid.UUID) -> None:
    exists = (
        await session.execute(
            select(Brand.id).where(Brand.id == brand_id, Brand.archived_at.is_(None))
        )
    ).scalar_one_or_none()
    if exists is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "brand not found")


async def _row(session: AsyncSession, source_id: uuid.UUID, *, lock: bool = False) -> SourceAsset:
    stmt = select(SourceAsset).where(SourceAsset.id == source_id)
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "source not found")
    return row


async def _find_by_checksum(
    session: AsyncSession, brand_id: uuid.UUID, checksum: str
) -> SourceAsset | None:
    return (
        await session.execute(
            select(SourceAsset).where(
                SourceAsset.brand_id == brand_id, SourceAsset.checksum == checksum
            )
        )
    ).scalar_one_or_none()


async def get_source(session: AsyncSession, source_id: uuid.UUID) -> SourceView:
    return _view(await _row(session, source_id))


async def get_source_content(
    session: AsyncSession, source_id: uuid.UUID, part: str
) -> tuple[SourceView, str | None]:
    row = await _row(session, source_id)
    content = {
        "raw": row.raw_content,
        "normalized": row.normalized_content,
        "transcript": row.transcript,
    }.get(part)
    if part not in {"raw", "normalized", "transcript"}:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE, "part must be raw|normalized|transcript"
        )
    return _view(row), content


async def list_sources(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID | None,
    source_type: SourceType | None,
    cursor: uuid.UUID | None,
    limit: int,
    include_archived: bool = False,
) -> tuple[list[SourceView], uuid.UUID | None]:
    stmt = select(SourceAsset).order_by(SourceAsset.id.desc()).limit(limit + 1)
    if brand_id is not None:
        stmt = stmt.where(SourceAsset.brand_id == brand_id)
    if source_type is not None:
        stmt = stmt.where(SourceAsset.source_type == source_type)
    if not include_archived:
        stmt = stmt.where(SourceAsset.archived_at.is_(None))
    if cursor is not None:
        stmt = stmt.where(SourceAsset.id < cursor)
    rows = list((await session.execute(stmt)).scalars())
    next_cursor = rows[limit - 1].id if len(rows) > limit else None
    return [_view(r) for r in rows[:limit]], next_cursor


# ------------------------------------------------------------------ writes
async def _create_pending(
    session: AsyncSession, *, actor: Actor, brand_id: uuid.UUID, row: dict[str, Any]
) -> uuid.UUID:
    source_id: uuid.UUID = row.setdefault("id", uuid7())
    await create_with_state(
        session,
        machine=SOURCE_ASSET,
        table=_SOURCES,
        audit_table=_AUDIT,
        row={**row, "brand_id": brand_id, "actor_type": actor.type, "actor_id": actor.id},
        trigger="create",
        actor=actor,
        correlation_id=_correlation(),
        brand_id=brand_id,
    )
    return source_id


def _normalized_values(doc: NormalizedDocument, title: str | None) -> dict[str, Any]:
    return {
        "normalized_content": doc.text,
        "transcript": doc.transcript,
        "title": title or doc.title,
        "checksum": doc.checksum,
        "metadata": {**doc.metadata, "sections": len(doc.sections)},
    }


async def _mark_normalized(
    session: AsyncSession, source_id: uuid.UUID, brand_id: uuid.UUID, values: dict[str, Any]
) -> None:
    await apply_transition(
        session,
        machine=SOURCE_ASSET,
        table=_SOURCES,
        audit_table=_AUDIT,
        entity_id=source_id,
        from_state=SourceAssetStatus.PENDING,
        trigger="normalized",
        actor=system_actor("ingestion"),
        correlation_id=_correlation(),
        brand_id=brand_id,
        values=values,
    )


async def ingest_text(
    session: AsyncSession,
    *,
    actor: Actor,
    brand_id: uuid.UUID,
    source_type: SourceType,
    content: str,
    title: str | None,
) -> AcceptedSource:
    if source_type not in TEXT_SOURCE_TYPES:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE, "source_type must be a text type for pasted content"
        )
    data = content.encode("utf-8")
    if len(data) > MAX_PASTE_BYTES:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE, "pasted text too large", details={"limit": "1 MiB"}
        )
    await _require_brand(session, brand_id)
    doc = parse(data, source_type=source_type)
    existing = await _find_by_checksum(session, brand_id, doc.checksum)
    if existing is not None:
        return AcceptedSource(source=_view(existing), duplicate=True)
    source_id = await _create_pending(
        session,
        actor=actor,
        brand_id=brand_id,
        row={
            "source_type": source_type,
            "raw_content": content,
            "mime_type": "text/plain; charset=utf-8",
            "byte_size": len(data),
            "checksum": doc.checksum,
        },
    )
    await _mark_normalized(session, source_id, brand_id, _normalized_values(doc, title))
    await session.commit()
    return AcceptedSource(source=await get_source(session, source_id), duplicate=False)


async def _enqueue_ingest(
    session: AsyncSession, queue: JobQueue, source: SourceView, actor: Actor
) -> uuid.UUID:
    job_id = await create_job(
        session,
        kind=JobKind.SOURCE_INGEST,
        actor=actor,
        brand_id=source.brand_id,
        subject_type="source_assets",
        subject_id=source.id,
    )
    await session.commit()  # the row must exist before a worker can pick the job up
    try:
        dispatch(queue, JobKind.SOURCE_INGEST, job_id)
    except OmniGrowthError as exc:
        # Nothing will ever run this job: close it and leave the source retryable.
        await cancel_queued(session, job_id, reason="queue unavailable")
        await record_failure(session, source.id, exc)
        await session.commit()
        raise
    return job_id


async def retry_source(
    session: AsyncSession, *, actor: Actor, queue: JobQueue, source_id: uuid.UUID
) -> AcceptedSource:
    """FAILED -> PENDING and a new SOURCE_INGEST job (pasted text is never FAILED)."""
    row = await _row(session, source_id, lock=True)
    await apply_transition(
        session,
        machine=SOURCE_ASSET,
        table=_SOURCES,
        audit_table=_AUDIT,
        entity_id=source_id,
        from_state=SourceAssetStatus(row.status),
        trigger="retry",
        actor=actor,
        correlation_id=_correlation(),
        brand_id=row.brand_id,
        values={"error_code": None},
    )
    source = await get_source(session, source_id)
    job_id = await _enqueue_ingest(session, queue, source, actor)
    return AcceptedSource(source=source, duplicate=False, job_id=job_id)


async def accept_upload(
    session: AsyncSession,
    *,
    actor: Actor,
    store: UploadStore,
    queue: JobQueue,
    brand_id: uuid.UUID,
    data: bytes,
    filename: str | None,
    declared_content_type: str | None,
    title: str | None,
) -> AcceptedSource:
    """Validate + store synchronously; the parse happens in the SOURCE_INGEST job."""
    if not data:
        raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "empty upload")
    if len(data) > DEFAULT_LIMITS.max_bytes:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE, "upload too large", details={"limit": "max_bytes"}
        )
    await _require_brand(session, brand_id)
    clean_name = safe_filename(filename)
    source_type = sniff_type(data, clean_name, declared_content_type)  # magic bytes must agree
    checksum = hashlib.sha256(data).hexdigest()
    existing = await _find_by_checksum(session, brand_id, checksum)
    if existing is not None:
        return AcceptedSource(source=_view(existing), duplicate=True)
    uri = store.put(data)
    source_id = await _create_pending(
        session,
        actor=actor,
        brand_id=brand_id,
        row={
            "source_type": source_type,
            "title": title,
            "storage_uri": uri,
            "mime_type": declared_content_type,
            "byte_size": len(data),
            "checksum": checksum,
            "metadata": {"filename": clean_name},
        },
    )
    source = await get_source(session, source_id)
    job_id = await _enqueue_ingest(session, queue, source, actor)
    return AcceptedSource(source=source, duplicate=False, job_id=job_id)


async def accept_url(
    session: AsyncSession,
    *,
    actor: Actor,
    queue: JobQueue,
    brand_id: uuid.UUID,
    url: str,
    source_type: SourceType,
    title: str | None,
) -> AcceptedSource:
    if source_type not in (SourceType.URL, SourceType.YOUTUBE_URL):
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE, "source_type must be URL or YOUTUBE_URL"
        )
    precheck_url(url)
    await _require_brand(session, brand_id)
    source_id = uuid7()
    # Content checksum is unknown until fetched; a per-row placeholder keeps the
    # (brand_id, checksum) uniqueness meaningful and is replaced on normalisation.
    placeholder = hashlib.sha256(f"pending-url:{source_id}".encode()).hexdigest()
    await _create_pending(
        session,
        actor=actor,
        brand_id=brand_id,
        row={
            "id": source_id,
            "source_type": source_type,
            "source_url": url,
            "title": title,
            "checksum": placeholder,
        },
    )
    source = await get_source(session, source_id)
    job_id = await _enqueue_ingest(session, queue, source, actor)
    return AcceptedSource(source=source, duplicate=False, job_id=job_id)


# ------------------------------------------------------------------ job processing
async def process_source(
    session: AsyncSession,
    source_id: uuid.UUID,
    *,
    store: UploadStore,
    fetcher: Fetcher = safe_fetch,
) -> dict[str, Any]:
    """Parse a PENDING source (upload or URL) and mark it NORMALIZED. Idempotent."""
    row = await _row(session, source_id, lock=True)
    if row.status != SourceAssetStatus.PENDING:
        return {"source_asset_id": str(source_id), "skipped": f"status {row.status}"}
    values: dict[str, Any]
    if row.storage_uri is not None:
        data = store.get(row.storage_uri)
        doc = parse(
            data,
            filename=(row.metadata_ or {}).get("filename"),
            declared_content_type=row.mime_type,
        )
        values = _normalized_values(doc, row.title)
    elif row.source_type == SourceType.YOUTUBE_URL:
        # No platform calls before Phase 13 (AGENTS.md §3).
        raise OmniGrowthError(
            ErrorCode.INGESTION_FAILURE,
            "YouTube ingestion is not implemented yet",
            details={"reason": "NOT_IMPLEMENTED"},
        )
    elif row.source_url is not None:
        fetched = await fetcher(row.source_url)
        if fetched.content_type in {"text/html", "application/xhtml+xml"}:
            doc = parse_html(fetched.body, base_url=fetched.final_url)
        else:
            doc = parse(fetched.body, declared_content_type=fetched.content_type)
        values = _normalized_values(doc, row.title)
        values["mime_type"] = fetched.content_type
        values["byte_size"] = len(fetched.body)
        values["metadata"] = {
            **values["metadata"],
            "final_url": fetched.final_url,
            "redirects": len(fetched.redirects),
        }
    else:
        raise OmniGrowthError(ErrorCode.INGESTION_FAILURE, "source has nothing to ingest")

    # Keep what was recorded at accept time (e.g. the sanitised filename).
    values["metadata"] = {**(row.metadata_ or {}), **values["metadata"]}
    duplicate = await _find_by_checksum(session, row.brand_id, values["checksum"])
    if duplicate is not None and duplicate.id != source_id:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "identical content already ingested for this brand",
            details={"duplicate_of": str(duplicate.id)},
        )
    await _mark_normalized(session, source_id, row.brand_id, values)
    return {"source_asset_id": str(source_id), "status": SourceAssetStatus.NORMALIZED.value}


async def record_failure(session: AsyncSession, source_id: uuid.UUID, exc: OmniGrowthError) -> None:
    """Terminal outcome for a failed ingest: UNSUPPORTED for not-yet-implemented media."""
    row = await _row(session, source_id, lock=True)
    if row.status != SourceAssetStatus.PENDING:
        return
    unsupported = exc.details.get("reason") == "NOT_IMPLEMENTED"
    await apply_transition(
        session,
        machine=SOURCE_ASSET,
        table=_SOURCES,
        audit_table=_AUDIT,
        entity_id=source_id,
        from_state=SourceAssetStatus.PENDING,
        trigger="unsupported" if unsupported else "failed",
        actor=system_actor("ingestion"),
        correlation_id=_correlation(),
        brand_id=row.brand_id,
        values={"error_code": exc.code},
        reason=exc.message,
    )


async def archive_source(session: AsyncSession, source_id: uuid.UUID, actor: Actor) -> SourceView:
    row = await _row(session, source_id, lock=True)
    if row.archived_at is None:
        row.archived_at = datetime.now(UTC)
        await record_audit(
            session,
            actor=actor,
            action="source_asset.archive",
            entity_type="source_assets",
            entity_id=source_id,
            brand_id=row.brand_id,
        )
        await session.commit()
    return _view(row)
