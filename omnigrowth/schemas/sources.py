"""Source ingestion + job API schemas (API_PLAN sources and jobs groups)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from omnigrowth.core.enums import JobKind, JobStatus, SourceAssetStatus, SourceType
from omnigrowth.core.errors import ErrorCode


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceTextCreate(_Request):
    brand_id: uuid.UUID
    source_type: SourceType = SourceType.TEXT
    title: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=1)


class SourceUrlIngestRequest(_Request):
    brand_id: uuid.UUID
    url: str = Field(min_length=8, max_length=2048)
    source_type: SourceType = SourceType.URL
    title: str | None = Field(default=None, max_length=500)


class SourceAssetRead(BaseModel):
    """Metadata only — bodies are served by ``/sources/{id}/content`` (API_PLAN)."""

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


class SourceContentRead(BaseModel):
    source_id: uuid.UUID
    part: str
    content: str | None


class JobRead(BaseModel):
    id: uuid.UUID
    kind: JobKind
    status: JobStatus
    subject_type: str | None
    subject_id: uuid.UUID | None
    result: dict[str, Any] | None
    error_code: ErrorCode | None
    error: dict[str, Any] | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
