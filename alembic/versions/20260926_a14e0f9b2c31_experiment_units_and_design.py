"""experiment units and pre-registered design (SPEC_AMENDMENTS A14)

One row per independent experimental unit (a post) and its variant; a JSONB design on the
experiment (unit, platform/accounts, observation age, allocation, stopping rule, MDE,
power, alpha, frozen sample plan); randomized vs observational assignment.

Revision ID: a14e0f9b2c31
Revises: 83579cc9b1d2
Create Date: 2026-09-26 21:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a14e0f9b2c31"
down_revision: str | None = "83579cc9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column(
            "design",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "experiments",
        sa.Column("assignment", sa.Text(), server_default=sa.text("'RANDOMIZED'"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_experiments_assignment_known"),
        "experiments",
        "assignment IN ('RANDOMIZED', 'OBSERVATIONAL')",
    )
    op.create_check_constraint(
        op.f("ck_experiments_observational_has_no_winner"),
        "experiments",
        "assignment = 'RANDOMIZED' OR winner_variant_id IS NULL",
    )
    op.create_table(
        "experiment_units",
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("content_asset_id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        sa.Column("block_key", sa.Text(), nullable=False),
        sa.Column("block_seq", sa.Integer(), nullable=False),
        sa.Column("assigned_by", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "assigned_by IN ('RANDOMIZATION', 'MANUAL')",
            name=op.f("ck_experiment_units_assigned_by_known"),
        ),
        sa.ForeignKeyConstraint(
            ["content_asset_id"],
            ["content_assets.id"],
            name=op.f("fk_experiment_units_content_asset_id_content_assets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name=op.f("fk_experiment_units_experiment_id_experiments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "variant_id"],
            ["experiment_variants.experiment_id", "experiment_variants.id"],
            name="fk_experiment_units_variant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["platform_account_id"],
            ["platform_accounts.id"],
            name=op.f("fk_experiment_units_platform_account_id_platform_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_units")),
        sa.UniqueConstraint(
            "experiment_id",
            "block_key",
            "block_seq",
            name=op.f("uq_experiment_units_experiment_id_block_key_block_seq"),
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "content_asset_id",
            name=op.f("uq_experiment_units_experiment_id_content_asset_id"),
        ),
    )
    op.create_index(
        "experiment_units_asset_idx", "experiment_units", ["content_asset_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("experiment_units_asset_idx", table_name="experiment_units")
    op.drop_table("experiment_units")
    op.drop_constraint(
        op.f("ck_experiments_observational_has_no_winner"), "experiments", type_="check"
    )
    op.drop_constraint(op.f("ck_experiments_assignment_known"), "experiments", type_="check")
    op.drop_column("experiments", "assignment")
    op.drop_column("experiments", "design")
