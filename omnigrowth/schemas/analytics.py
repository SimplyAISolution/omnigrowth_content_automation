"""Analytics and recommendation API schemas (API_PLAN §2.10, §2.11)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from omnigrowth.core.enums import ActorType, MetricSource, RecommendationStatus


class _Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SnapshotIn(BaseModel):
    """One fixture/manual snapshot. Omitted metrics are NULL ("not provided"), never 0."""

    model_config = ConfigDict(extra="forbid")

    publication_id: uuid.UUID
    captured_at: datetime
    source: MetricSource = MetricSource.FIXTURE
    impressions: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    reach: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    profile_visits: int | None = Field(default=None, ge=0)
    followers_gained: int | None = None  # net; may be negative
    conversion_count: int | None = Field(default=None, ge=0)

    @field_validator("captured_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("captured_at must include a timezone offset")
        return value

    @field_validator("source")
    @classmethod
    def _not_api(cls, value: MetricSource) -> MetricSource:
        if value in (MetricSource.API, MetricSource.MOCK):
            raise ValueError("API and MOCK snapshots come from /analytics/pull, not uploads")
        return value


class AnalyticsSnapshotBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SnapshotIn] = Field(min_length=1, max_length=500)


class AnalyticsSnapshotIngestResultRead(BaseModel):
    created: list[uuid.UUID]
    duplicates: int


class AnalyticsPullRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: uuid.UUID


class AnalyticsSnapshotRead(_Read):
    id: uuid.UUID
    brand_id: uuid.UUID
    publication_id: uuid.UUID
    captured_at: datetime
    source: MetricSource
    impressions: int | None
    views: int | None
    reach: int | None
    likes: int | None
    comments: int | None
    shares: int | None
    saves: int | None
    clicks: int | None
    profile_visits: int | None
    followers_gained: int | None
    conversion_count: int | None
    ctr: Decimal | None
    created_at: datetime


class CampaignReportRead(BaseModel):
    campaign_id: uuid.UUID
    coverage: dict[str, int]
    totals: dict[str, int | None]
    derived: dict[str, dict[str, Any]]
    by_platform: dict[str, dict[str, Any]]
    publications: list[dict[str, Any]]
    definitions: list[dict[str, Any]]


class RecommendationGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: uuid.UUID


class RecommendationRead(_Read):
    id: uuid.UUID
    brand_id: uuid.UUID
    campaign_id: uuid.UUID | None
    agent_run_id: uuid.UUID
    statement: str
    rationale: str | None
    target: str | None
    evidence_snapshot_ids: list[uuid.UUID]
    evidence_query: dict[str, Any]
    status: RecommendationStatus
    decided_by_actor_type: ActorType | None = None
    decided_by_actor_id: str | None = None
    created_at: datetime
