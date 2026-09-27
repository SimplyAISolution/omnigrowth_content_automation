"""Content API schemas (API_PLAN §2.6): assets, versions (§36), diffs, QA results (§11),
similarity (§10)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from omnigrowth.core.enums import (
    ActorType,
    AngleType,
    ContentAssetStatus,
    ContentFormat,
    ContentVersionOrigin,
    FunnelStage,
    Platform,
    QaOutcome,
    QaStage,
)


class _Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AssetGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_item_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)


class ContentVersionCreate(BaseModel):
    """Human edit (§36). Omitted optional fields keep the base version's value. For X and
    Mastodon the body holds the posts separated by lines containing only ``---``.
    ``platform_fields`` changes platform-specific fields (scenes, script sections, chapters,
    subreddit, ...; see ``omnigrowth.agents.writers.*Extras``): keys given replace the base
    version's, and the result is validated for the asset's platform."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    based_on_version_id: uuid.UUID
    body: str = Field(min_length=1, max_length=20_000)
    hook: str | None = Field(default=None, max_length=600)
    cta: str | None = Field(default=None, max_length=400)
    title: str | None = Field(default=None, max_length=300)
    hashtags: list[str] | None = Field(default=None, max_length=10)
    platform_fields: dict[str, Any] | None = None
    comment: str | None = Field(default=None, max_length=2000)


class ContentVersionRead(_Read):
    id: uuid.UUID
    content_asset_id: uuid.UUID
    version_no: int
    origin: ContentVersionOrigin
    based_on_version_id: uuid.UUID | None
    title: str | None
    hook: str | None
    body: str
    cta: str | None
    media_requirements: list[Any]
    platform_payload: dict[str, Any]
    body_hash: str
    word_count: int
    char_count: int
    hook_type: str | None
    cta_type: str | None
    prompt_version_id: uuid.UUID | None
    model_run_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    author_actor_type: ActorType
    author_actor_id: str
    created_at: datetime


class QaResultRead(_Read):
    id: uuid.UUID
    content_version_id: uuid.UUID
    stage: QaStage
    outcome: QaOutcome
    score: Decimal | None
    findings: list[Any]
    checker: str
    created_at: datetime


class ContentAssetRead(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    campaign_id: uuid.UUID
    plan_item_id: uuid.UUID | None
    content_idea_id: uuid.UUID | None
    platform: Platform
    format: ContentFormat
    status: ContentAssetStatus
    angle: AngleType | None
    funnel_stage: FunnelStage | None
    current_version_id: uuid.UUID | None
    approved_version_id: uuid.UUID | None
    # Repurposing graph (§9): a derivative knows its parent and the version it came from.
    parent_asset_id: uuid.UUID | None = None
    root_asset_id: uuid.UUID | None = None
    lineage_depth: int = 0
    source_version_id: uuid.UUID | None = None
    repurpose_instructions: str | None = None
    quality_score: Decimal | None
    created_at: datetime
    updated_at: datetime
    current_version: ContentVersionRead | None = None
    qa: list[QaResultRead] = Field(default_factory=list)

    @classmethod
    def asset_columns(cls) -> list[str]:
        return [f for f in cls.model_fields if f not in {"current_version", "qa"}]


class FieldChangeRead(BaseModel):
    field: str
    before: Any
    after: Any


class ContentVersionDiffRead(BaseModel):
    from_version_id: uuid.UUID
    to_version_id: uuid.UUID
    from_version_no: int
    to_version_no: int
    changes: list[FieldChangeRead]
    unified: str


class SimilarContentRead(BaseModel):
    content_asset_id: uuid.UUID
    content_version_id: uuid.UUID
    similarity: float
    near_duplicate: bool


class RegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content_version_id: uuid.UUID
    instructions: str | None = Field(default=None, max_length=4000)


class RepurposeRequest(BaseModel):
    """Derive a new asset for another platform/format from this asset's current version."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_platform: Platform
    target_format: ContentFormat
    instructions: str | None = Field(default=None, max_length=4000)
