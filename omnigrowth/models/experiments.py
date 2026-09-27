"""Experiments: experiments, experiment_variants (DATA_MODEL §5.5, §5.6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from omnigrowth.core.enums import ExperimentStatus, ExperimentVariable
from omnigrowth.models._columns import TZ, CreatedAtMixin, fk
from omnigrowth.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin, pg_enum


class Experiment(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (
        # Winner must be a variant of *this* experiment. use_alter breaks the
        # experiments <-> experiment_variants FK cycle for CREATE ordering.
        ForeignKeyConstraint(
            ["id", "winner_variant_id"],
            ["experiment_variants.experiment_id", "experiment_variants.id"],
            name="fk_experiments_winner_variant",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            "(metric_definition_id IS NULL) <> (raw_metric IS NULL)", name="one_metric_source"
        ),
        CheckConstraint(
            "winner_variant_id IS NULL OR status = 'CONCLUDED'", name="winner_only_concluded"
        ),
        CheckConstraint("assignment IN ('RANDOMIZED', 'OBSERVATIONAL')", name="assignment_known"),
        # A nonrandomized comparison can never declare a winner (A14).
        CheckConstraint(
            "assignment = 'RANDOMIZED' OR winner_variant_id IS NULL",
            name="observational_has_no_winner",
        ),
        Index("experiments_brand_status_idx", "brand_id", "status"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(fk("campaigns.id"), nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    variable: Mapped[ExperimentVariable] = mapped_column(
        pg_enum(ExperimentVariable), nullable=False
    )
    metric_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("derived_metric_definitions.id"), nullable=True
    )
    raw_metric: Mapped[str | None] = mapped_column(Text, nullable=True)
    min_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_threshold: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default=text("0.950")
    )
    # Written only by omnigrowth.core.state_machines (AGENTS.md §4).
    status: Mapped[ExperimentStatus] = mapped_column(
        pg_enum(ExperimentStatus), nullable=False, server_default=ExperimentStatus.DRAFT.value
    )
    start_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    winner_variant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    concluded_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    # Pre-registered design (A14): unit, platform/accounts, observation age, allocation,
    # stopping rule, MDE, power, alpha, seed; the sample plan is added at start and frozen.
    design: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    assignment: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'RANDOMIZED'")
    )


class ExperimentVariant(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "experiment_variants"
    __table_args__ = (
        UniqueConstraint("experiment_id", "label"),
        UniqueConstraint("experiment_id", "id"),
        Index("experiment_variants_asset_idx", "content_asset_id"),
    )

    experiment_id: Mapped[uuid.UUID] = mapped_column(fk("experiments.id"), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    content_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("content_assets.id"), nullable=True
    )
    sample_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    observed_value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)


class ExperimentUnit(UUIDPkMixin, CreatedAtMixin, Base):
    """One independent experimental unit = one post (content asset) assigned to one variant.

    Assignment is immutable (no updated_at): randomized units are allocated by the server
    (blocked by platform account), observational units are labelled by a human.
    """

    __tablename__ = "experiment_units"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id", "variant_id"],
            ["experiment_variants.experiment_id", "experiment_variants.id"],
            name="fk_experiment_units_variant",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("experiment_id", "content_asset_id"),
        UniqueConstraint("experiment_id", "block_key", "block_seq"),
        CheckConstraint("assigned_by IN ('RANDOMIZATION', 'MANUAL')", name="assigned_by_known"),
        Index("experiment_units_asset_idx", "content_asset_id"),
    )

    experiment_id: Mapped[uuid.UUID] = mapped_column(fk("experiments.id"), nullable=False)
    variant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    content_asset_id: Mapped[uuid.UUID] = mapped_column(fk("content_assets.id"), nullable=False)
    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        fk("platform_accounts.id"), nullable=False
    )
    block_key: Mapped[str] = mapped_column(Text, nullable=False)
    block_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    assigned_by: Mapped[str] = mapped_column(Text, nullable=False)
