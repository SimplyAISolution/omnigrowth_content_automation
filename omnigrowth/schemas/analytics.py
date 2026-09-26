"""Analytics and recommendation API schemas (API_PLAN §2.10, §2.11)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from omnigrowth.core.enums import (
    ActorType,
    CalendarItemKind,
    ContentAssetStatus,
    ContentFormat,
    MetricNullReason,
    MetricSource,
    Platform,
    PublicationJobStatus,
    RecommendationStatus,
)


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


SIMULATED_SOURCES = frozenset({MetricSource.MOCK, MetricSource.FIXTURE})
CORRELATION_NOTE = (
    "Correlational, not causal: groups differ in more than the compared attribute "
    "(topic, timing, audience, platform algorithm changes). Treat as a hypothesis to test "
    "with an experiment, not as proof."
)


class DataProvenance(BaseModel):
    """Where the numbers came from. MOCK/FIXTURE data is simulated and must never be read
    as real platform performance."""

    sources: dict[MetricSource, int]
    simulated: bool
    real: bool
    note: str | None = None


class CampaignReportRead(BaseModel):
    campaign_id: uuid.UUID
    range: dict[str, datetime | None]
    coverage: dict[str, int]
    totals: dict[str, int | None]
    derived: dict[str, dict[str, Any]]
    by_platform: dict[str, dict[str, Any]]
    by_format: dict[str, dict[str, Any]]
    publications: list[dict[str, Any]]
    definitions: list[dict[str, Any]]
    provenance: DataProvenance
    sufficiency: dict[str, Any]


class DerivedMetricDefinitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand_id: uuid.UUID
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    formula: str = Field(min_length=1, max_length=1000)
    weights: dict[str, float] = Field(default_factory=dict, max_length=20)


class DerivedMetricDefinitionUpdate(BaseModel):
    """Any change creates a new revision; omitted fields are carried over."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    formula: str | None = Field(default=None, min_length=1, max_length=1000)
    weights: dict[str, float] | None = Field(default=None, max_length=20)


class DerivedMetricDefinitionRead(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID | None
    key: str
    name: str
    description: str | None
    formula: str
    weights: dict[str, float]
    metrics: list[str]
    revision: int
    is_active: bool
    is_default: bool
    created_by: str
    created_at: datetime


class DerivedValueRead(BaseModel):
    definition_id: uuid.UUID
    key: str
    revision: int
    value: float | None
    null_reason: str | None
    null_code: MetricNullReason | None
    missing_inputs: list[str]


class PublicationMetricsRead(BaseModel):
    publication_id: uuid.UUID
    latest_snapshot_id: uuid.UUID | None
    raw: AnalyticsSnapshotRead | None
    inputs: dict[str, float | None]
    derived: list[DerivedValueRead]
    provenance: DataProvenance


class AttributeGroupRead(BaseModel):
    value: str | None
    n: int
    excluded_null: int
    mean: float | None
    median: float | None
    sufficient: bool
    snapshot_ids: list[uuid.UUID]
    sources: dict[MetricSource, int]


class AttributeComparisonRead(BaseModel):
    attribute: str
    metric: str
    definition: dict[str, Any] | None
    filters: dict[str, Any]
    min_sample: int
    groups: list[AttributeGroupRead]
    provenance: DataProvenance
    note: str


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


# ------------------------------------------------------------------ calendar (§13, API_PLAN §3.7)
class CalendarItemRead(BaseModel):
    kind: CalendarItemKind
    id: uuid.UUID
    title: str
    platform: Platform
    format: ContentFormat
    content_asset_id: uuid.UUID | None
    content_asset_status: ContentAssetStatus | None
    publication_job_status: PublicationJobStatus | None
    due_at: datetime | None
    window_start: datetime | None
    window_end: datetime | None
    campaign_id: uuid.UUID
    campaign_name: str


class CalendarRead(BaseModel):
    view: str
    tz: str
    range_start: datetime
    range_end: datetime
    items: list[CalendarItemRead]
