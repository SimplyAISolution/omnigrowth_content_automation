"""Content domain: content_ideas, content_assets, content_versions, qa_results,
approval_actions (DATA_MODEL §4.6, §4.8-§4.11)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from omnigrowth.core.enums import (
    ActorType,
    AngleType,
    ApprovalActionType,
    ContentAssetStatus,
    ContentFormat,
    ContentIdeaStatus,
    ContentVersionOrigin,
    FunnelStage,
    Platform,
    QaOutcome,
    QaStage,
)
from omnigrowth.models._columns import (
    EMPTY_ARRAY,
    EMPTY_JSON_ARRAY,
    EMPTY_JSON_OBJECT,
    TZ,
    ZERO,
    CreatedAtMixin,
    fk,
    sha256_check,
)
from omnigrowth.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin, pg_enum


class ContentIdea(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "content_ideas"
    __table_args__ = (Index("content_ideas_campaign_angle_idx", "campaign_id", "angle"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(fk("campaigns.id"), nullable=False)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("agent_runs.id"), nullable=True)
    angle: Mapped[AngleType] = mapped_column(pg_enum(AngleType), nullable=False)
    hook: Mapped[str] = mapped_column(Text, nullable=False)
    audience_id: Mapped[uuid.UUID | None] = mapped_column(fk("audiences.id"), nullable=True)
    platform_targets: Mapped[list[Platform]] = mapped_column(
        ARRAY(pg_enum(Platform)), nullable=False, server_default=EMPTY_ARRAY
    )
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    evidence: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_ARRAY
    )
    status: Mapped[ContentIdeaStatus] = mapped_column(
        pg_enum(ContentIdeaStatus),
        nullable=False,
        server_default=ContentIdeaStatus.PROPOSED.value,
    )


class ContentAsset(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    """Mutable header of a piece of content; text lives in ``content_versions``.

    Lineage immutability (``BEFORE UPDATE`` trigger) and insert-time lineage consistency
    (``BEFORE INSERT``: root and depth follow from the parent) are created in the Alembic
    migrations, not here.
    """

    __tablename__ = "content_assets"
    __table_args__ = (
        UniqueConstraint("brand_id", "id"),
        UniqueConstraint("plan_item_id"),
        ForeignKeyConstraint(
            ["brand_id", "parent_asset_id"],
            ["content_assets.brand_id", "content_assets.id"],
            name="fk_content_assets_parent_same_brand",
            ondelete="RESTRICT",
        ),
        # Pointed-to versions must belong to this asset. use_alter breaks the
        # content_assets <-> content_versions FK cycle for CREATE ordering.
        ForeignKeyConstraint(
            ["id", "current_version_id"],
            ["content_versions.content_asset_id", "content_versions.id"],
            name="fk_content_assets_current_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["id", "approved_version_id"],
            ["content_versions.content_asset_id", "content_versions.id"],
            name="fk_content_assets_approved_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint("parent_asset_id IS NULL OR parent_asset_id <> id", name="parent_not_self"),
        CheckConstraint(
            "(parent_asset_id IS NULL) = (root_asset_id = id)", name="root_iff_no_parent"
        ),
        CheckConstraint("lineage_depth BETWEEN 0 AND 5", name="lineage_depth_range"),
        # A derivative records which version of its parent it was made from (Phase 10).
        ForeignKeyConstraint(
            ["parent_asset_id", "source_version_id"],
            ["content_versions.content_asset_id", "content_versions.id"],
            name="fk_content_assets_source_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            "(parent_asset_id IS NULL) = (source_version_id IS NULL)", name="source_iff_parent"
        ),
        Index(
            "content_assets_one_derivative_idx",
            "parent_asset_id",
            "platform",
            "format",
            unique=True,
            postgresql_where=text("parent_asset_id IS NOT NULL AND archived_at IS NULL"),
        ),
        Index(
            "content_assets_campaign_status_idx",
            "campaign_id",
            "status",
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index(
            "content_assets_parent_idx",
            "parent_asset_id",
            postgresql_where=text("parent_asset_id IS NOT NULL"),
        ),
        Index("content_assets_root_idx", "root_asset_id"),
        Index("content_assets_attr_idx", "brand_id", "platform", "format"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(fk("campaigns.id"), nullable=False)
    plan_item_id: Mapped[uuid.UUID | None] = mapped_column(fk("plan_items.id"), nullable=True)
    content_idea_id: Mapped[uuid.UUID | None] = mapped_column(fk("content_ideas.id"), nullable=True)
    # Same-brand parent enforced by the composite FK above.
    parent_asset_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    repurpose_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_asset_id: Mapped[uuid.UUID] = mapped_column(fk("content_assets.id"), nullable=False)
    lineage_depth: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=ZERO)
    platform: Mapped[Platform] = mapped_column(pg_enum(Platform), nullable=False)
    format: Mapped[ContentFormat] = mapped_column(pg_enum(ContentFormat), nullable=False)
    # Written only by omnigrowth.core.state_machines (AGENTS.md §4).
    status: Mapped[ContentAssetStatus] = mapped_column(
        pg_enum(ContentAssetStatus),
        nullable=False,
        server_default=ContentAssetStatus.DRAFT.value,
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    generation_request_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    auto_regeneration_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=ZERO
    )
    approved_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    audience_id: Mapped[uuid.UUID | None] = mapped_column(fk("audiences.id"), nullable=True)
    offer_id: Mapped[uuid.UUID | None] = mapped_column(fk("offers.id"), nullable=True)
    pillar: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    angle: Mapped[AngleType | None] = mapped_column(pg_enum(AngleType), nullable=True)
    funnel_stage: Mapped[FunnelStage | None] = mapped_column(pg_enum(FunnelStage), nullable=True)


Index(
    "content_assets_brand_status_idx",
    ContentAsset.brand_id,
    ContentAsset.status,
    ContentAsset.updated_at.desc(),
    postgresql_where=text("archived_at IS NULL"),
)


class ContentVersion(UUIDPkMixin, CreatedAtMixin, Base):
    """Immutable. UPDATE/DELETE-blocking trigger is created in the Alembic migration."""

    __tablename__ = "content_versions"
    __table_args__ = (
        UniqueConstraint("content_asset_id", "version_no"),
        UniqueConstraint("content_asset_id", "id"),
        sha256_check("body_hash"),
        CheckConstraint(
            "origin = 'HUMAN_EDIT' OR (prompt_version_id IS NOT NULL AND model_run_id IS NOT NULL)",
            name="generated_has_provenance",
        ),
        CheckConstraint(
            "origin <> 'GENERATED' OR based_on_version_id IS NULL", name="generated_no_base"
        ),
        Index("content_versions_prompt_idx", "prompt_version_id"),
        Index("content_versions_model_run_idx", "model_run_id"),
    )

    content_asset_id: Mapped[uuid.UUID] = mapped_column(fk("content_assets.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[ContentVersionOrigin] = mapped_column(
        pg_enum(ContentVersionOrigin), nullable=False
    )
    based_on_version_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("content_versions.id"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    cta: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_requirements: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_ARRAY
    )
    platform_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_OBJECT
    )
    body_hash: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    hook_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("prompt_versions.id"), nullable=True
    )
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("model_runs.id"), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("agent_runs.id"), nullable=True)
    brand_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("brand_profiles.id"), nullable=True
    )
    author_actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType), nullable=False)
    author_actor_id: Mapped[str] = mapped_column(Text, nullable=False)


class QaResult(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "qa_results"
    __table_args__ = (UniqueConstraint("content_version_id", "stage"),)

    content_version_id: Mapped[uuid.UUID] = mapped_column(fk("content_versions.id"), nullable=False)
    stage: Mapped[QaStage] = mapped_column(pg_enum(QaStage), nullable=False)
    outcome: Mapped[QaOutcome] = mapped_column(pg_enum(QaOutcome), nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    findings: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_ARRAY
    )
    checker: Mapped[str] = mapped_column(Text, nullable=False)
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("model_runs.id"), nullable=True)
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("prompt_versions.id"), nullable=True
    )


Index(
    "qa_results_stage_outcome_idx",
    QaResult.stage,
    QaResult.outcome,
    QaResult.created_at.desc(),
)


class ApprovalAction(UUIDPkMixin, CreatedAtMixin, Base):
    """Append-only. UPDATE/DELETE-blocking trigger is created in the Alembic migration."""

    __tablename__ = "approval_actions"
    __table_args__ = (
        CheckConstraint(
            "action <> 'SCHEDULE' OR scheduled_for IS NOT NULL", name="schedule_has_time"
        ),
        Index("approval_actions_asset_time_idx", "content_asset_id", "created_at"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    content_asset_id: Mapped[uuid.UUID] = mapped_column(fk("content_assets.id"), nullable=False)
    content_version_id: Mapped[uuid.UUID] = mapped_column(fk("content_versions.id"), nullable=False)
    resulting_version_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("content_versions.id"), nullable=True
    )
    action: Mapped[ApprovalActionType] = mapped_column(pg_enum(ApprovalActionType), nullable=False)
    from_status: Mapped[ContentAssetStatus] = mapped_column(
        pg_enum(ContentAssetStatus), nullable=False
    )
    to_status: Mapped[ContentAssetStatus] = mapped_column(
        pg_enum(ContentAssetStatus), nullable=False
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    platform_account_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("platform_accounts.id"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType), nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)


Index(
    "approval_actions_actor_time_idx",
    ApprovalAction.actor_id,
    ApprovalAction.created_at.desc(),
)
