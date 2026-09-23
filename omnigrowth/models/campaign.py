"""Campaign domain: campaigns, campaign_sources, campaign_strategies, plan_items
(DATA_MODEL §4.2, §4.2a, §4.3, §4.7)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from omnigrowth.core.enums import (
    ActorType,
    AngleType,
    CampaignObjective,
    CampaignStatus,
    ContentFormat,
    FunnelStage,
    Platform,
    RoutingProfile,
)
from omnigrowth.models._columns import EMPTY_ARRAY, TZ, ZERO, CreatedAtMixin, fk
from omnigrowth.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin, pg_enum


class Campaign(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint("priority BETWEEN 1 AND 5", name="priority_range"),
        CheckConstraint("end_date >= start_date", name="date_order"),
        CheckConstraint("duplicated_from_id <> id", name="not_self_duplicate"),
        Index(
            "campaigns_brand_status_idx",
            "brand_id",
            "status",
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index(
            "campaigns_duplicated_from_idx",
            "duplicated_from_id",
            postgresql_where=text("duplicated_from_id IS NOT NULL"),
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[CampaignObjective] = mapped_column(pg_enum(CampaignObjective), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    primary_platform: Mapped[Platform] = mapped_column(pg_enum(Platform), nullable=False)
    secondary_platforms: Mapped[list[Platform]] = mapped_column(
        ARRAY(pg_enum(Platform)), nullable=False, server_default=EMPTY_ARRAY
    )
    audience_id: Mapped[uuid.UUID | None] = mapped_column(fk("audiences.id"), nullable=True)
    offer_id: Mapped[uuid.UUID | None] = mapped_column(fk("offers.id"), nullable=True)
    content_pillar: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Written only by omnigrowth.core.state_machines (AGENTS.md §4).
    status: Mapped[CampaignStatus] = mapped_column(
        pg_enum(CampaignStatus), nullable=False, server_default=CampaignStatus.DRAFT.value
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("3"))
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    timezone: Mapped[str | None] = mapped_column(Text, nullable=True)
    routing_profile: Mapped[RoutingProfile] = mapped_column(pg_enum(RoutingProfile), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")
    budget_limit_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    duplicated_from_id: Mapped[uuid.UUID | None] = mapped_column(fk("campaigns.id"), nullable=True)
    actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType), nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)


Index("campaigns_brand_created_idx", Campaign.brand_id, Campaign.created_at.desc())


class CampaignSource(CreatedAtMixin, Base):
    """Campaign <-> source_asset link (composite PK, no surrogate id)."""

    __tablename__ = "campaign_sources"
    __table_args__ = (
        UniqueConstraint("campaign_id", "position"),
        CheckConstraint("position >= 0", name="position_nonneg"),
        Index("campaign_sources_source_idx", "source_asset_id"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(fk("campaigns.id"), primary_key=True)
    source_asset_id: Mapped[uuid.UUID] = mapped_column(fk("source_assets.id"), primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=ZERO)


class CampaignStrategy(UUIDPkMixin, CreatedAtMixin, Base):
    """Immutable row; a strategy change is a new version."""

    __tablename__ = "campaign_strategies"
    __table_args__ = (
        UniqueConstraint("campaign_id", "version_no"),
        Index(
            "campaign_strategies_active_uq",
            "campaign_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(fk("campaigns.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("agent_runs.id"), nullable=True)
    strategy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    positioning: Mapped[str] = mapped_column(Text, nullable=False)
    funnel_stage: Mapped[FunnelStage] = mapped_column(pg_enum(FunnelStage), nullable=False)
    primary_cta: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_platform: Mapped[Platform] = mapped_column(pg_enum(Platform), nullable=False)
    secondary_platforms: Mapped[list[Platform]] = mapped_column(
        ARRAY(pg_enum(Platform)), nullable=False, server_default=EMPTY_ARRAY
    )
    actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType), nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)


class PlanItem(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "plan_items"
    __table_args__ = (
        UniqueConstraint("campaign_id", "horizon_days", "ordinal"),
        CheckConstraint("horizon_days IN (7, 14, 30)", name="horizon_days_allowed"),
        CheckConstraint("depends_on_plan_item_id <> id", name="not_self_dependent"),
        CheckConstraint("window_end > window_start", name="window_order"),
        Index("plan_items_campaign_window_idx", "campaign_id", "window_start"),
        Index(
            "plan_items_brand_window_idx",
            "brand_id",
            "window_start",
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(fk("campaigns.id"), nullable=False)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("agent_runs.id"), nullable=True)
    horizon_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    platform: Mapped[Platform] = mapped_column(pg_enum(Platform), nullable=False)
    format: Mapped[ContentFormat] = mapped_column(pg_enum(ContentFormat), nullable=False)
    angle: Mapped[AngleType] = mapped_column(pg_enum(AngleType), nullable=False)
    objective: Mapped[CampaignObjective] = mapped_column(pg_enum(CampaignObjective), nullable=False)
    funnel_stage: Mapped[FunnelStage] = mapped_column(pg_enum(FunnelStage), nullable=False)
    cta: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(fk("source_assets.id"), nullable=True)
    content_idea_id: Mapped[uuid.UUID | None] = mapped_column(fk("content_ideas.id"), nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    depends_on_plan_item_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("plan_items.id"), nullable=True
    )
    window_start: Mapped[datetime] = mapped_column(TZ, nullable=False)
    window_end: Mapped[datetime] = mapped_column(TZ, nullable=False)
