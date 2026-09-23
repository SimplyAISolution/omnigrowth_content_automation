"""Analytics: analytics_snapshots, derived_metric_definitions, recommendations
(DATA_MODEL §5.3, §5.4, §5.7)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnigrowth.core.enums import ActorType, MetricSource, RecommendationStatus
from omnigrowth.models._columns import EMPTY_JSON_OBJECT, TZ, CreatedAtMixin, fk
from omnigrowth.models.base import Base, TimestampMixin, UUIDPkMixin, pg_enum

# Non-negative count metrics (every count except followers_gained, which is net).
NON_NEGATIVE_COUNTS = (
    "impressions",
    "views",
    "reach",
    "likes",
    "comments",
    "shares",
    "saves",
    "clicks",
    "profile_visits",
    "conversion_count",
)


class AnalyticsSnapshot(UUIDPkMixin, CreatedAtMixin, Base):
    """Immutable time series. ALL metrics nullable with NO default: NULL = not captured,
    never 0 (DATA_MODEL §5.3, Phase 8 gate)."""

    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        UniqueConstraint("publication_id", "captured_at"),
        *(
            CheckConstraint(f"{c} IS NULL OR {c} >= 0", name=f"{c}_nonneg")
            for c in NON_NEGATIVE_COUNTS
        ),
        CheckConstraint("ctr IS NULL OR ctr BETWEEN 0 AND 1", name="ctr_range"),
        CheckConstraint(
            "(revenue IS NULL) = (revenue_currency IS NULL)", name="revenue_currency_pair"
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    publication_id: Mapped[uuid.UUID] = mapped_column(fk("publications.id"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    source: Mapped[MetricSource] = mapped_column(pg_enum(MetricSource), nullable=False)
    impressions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reach: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    likes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comments: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    saves: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    clicks: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    profile_visits: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    followers_gained: Mapped[int | None] = mapped_column(Integer, nullable=True)
    watch_time_seconds: Mapped[Decimal | None] = mapped_column(Numeric(16, 3), nullable=True)
    average_view_duration_seconds: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3), nullable=True
    )
    ctr: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    conversion_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    revenue_currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    platform_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_OBJECT
    )
    raw_payload: Mapped[Any | None] = mapped_column(JSONB, nullable=True)


Index(
    "analytics_snapshots_brand_captured_idx",
    AnalyticsSnapshot.brand_id,
    AnalyticsSnapshot.captured_at.desc(),
)


class DerivedMetricDefinition(UUIDPkMixin, CreatedAtMixin, Base):
    """Immutable row; edit = new version. ``formula`` is a restricted AST, never code."""

    __tablename__ = "derived_metric_definitions"
    __table_args__ = (
        UniqueConstraint("brand_id", "key", "version_no", postgresql_nulls_not_distinct=True),
        Index(
            "derived_metric_definitions_active_uq",
            "brand_id",
            "key",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("is_active"),
        ),
    )

    brand_id: Mapped[uuid.UUID | None] = mapped_column(fk("brands.id"), nullable=True)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType), nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)


class Recommendation(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        CheckConstraint("cardinality(evidence_snapshot_ids) >= 1", name="has_evidence"),
        Index(
            "recommendations_evidence_gin",
            "evidence_snapshot_ids",
            postgresql_using="gin",
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(fk("campaigns.id"), nullable=True)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(fk("agent_runs.id"), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Arrays cannot carry FKs; ids are app-validated against analytics_snapshots (OQ-11).
    evidence_snapshot_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    evidence_query: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[RecommendationStatus] = mapped_column(
        pg_enum(RecommendationStatus),
        nullable=False,
        server_default=RecommendationStatus.PROPOSED.value,
    )
    decided_by_actor_type: Mapped[ActorType | None] = mapped_column(
        pg_enum(ActorType), nullable=True
    )
    decided_by_actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)


Index(
    "recommendations_brand_status_idx",
    Recommendation.brand_id,
    Recommendation.status,
    Recommendation.created_at.desc(),
)
