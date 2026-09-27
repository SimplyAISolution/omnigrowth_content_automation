"""repurposing lineage: source version, instructions, insert-time consistency (Phase 10, §9)

- ``content_assets.source_version_id``: the parent's version a derivative was made from
  (composite FK: the version must belong to the parent); present exactly when a parent is.
- ``content_assets.repurpose_instructions``: the human's instructions for the writer.
- An INSERT trigger checks that a child's ``root_asset_id`` and ``lineage_depth`` follow
  from its parent, so the stored graph cannot be inconsistent even if application code is.
- The existing immutability trigger now also covers ``source_version_id``: lineage is set
  at insert and never changes, which (with the parent FK) makes cycles impossible.
- One live derivative per (parent, platform, format): duplicate siblings are refused.

Revision ID: b10c4e7d9a52
Revises: a14e0f9b2c31
Create Date: 2026-09-27 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b10c4e7d9a52"
down_revision: str | None = "a14e0f9b2c31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("content_assets", sa.Column("source_version_id", sa.Uuid(), nullable=True))
    op.add_column("content_assets", sa.Column("repurpose_instructions", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_content_assets_source_version",
        "content_assets",
        "content_versions",
        ["parent_asset_id", "source_version_id"],
        ["content_asset_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_content_assets_source_iff_parent"),
        "content_assets",
        "(parent_asset_id IS NULL) = (source_version_id IS NULL)",
    )
    op.create_index(
        "content_assets_one_derivative_idx",
        "content_assets",
        ["parent_asset_id", "platform", "format"],
        unique=True,
        postgresql_where=sa.text("parent_asset_id IS NOT NULL AND archived_at IS NULL"),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION og_forbid_lineage_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.parent_asset_id IS DISTINCT FROM OLD.parent_asset_id
               OR NEW.root_asset_id IS DISTINCT FROM OLD.root_asset_id
               OR NEW.lineage_depth IS DISTINCT FROM OLD.lineage_depth
               OR NEW.source_version_id IS DISTINCT FROM OLD.source_version_id THEN
                RAISE EXCEPTION 'content_assets lineage is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION og_check_lineage_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            parent_root uuid;
            parent_depth smallint;
        BEGIN
            IF NEW.parent_asset_id IS NULL THEN
                IF NEW.lineage_depth <> 0 THEN
                    RAISE EXCEPTION 'a lineage root has depth 0'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            SELECT root_asset_id, lineage_depth INTO parent_root, parent_depth
              FROM content_assets WHERE id = NEW.parent_asset_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'parent asset does not exist'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF NEW.root_asset_id IS DISTINCT FROM parent_root
               OR NEW.lineage_depth IS DISTINCT FROM parent_depth + 1 THEN
                RAISE EXCEPTION 'derivative lineage must follow its parent (root %, depth %)',
                    parent_root, parent_depth + 1
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        """
    )
    op.execute(
        "CREATE TRIGGER content_assets_lineage_consistent BEFORE INSERT ON content_assets "
        "FOR EACH ROW EXECUTE FUNCTION og_check_lineage_insert()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS content_assets_lineage_consistent ON content_assets")
    op.execute("DROP FUNCTION IF EXISTS og_check_lineage_insert()")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION og_forbid_lineage_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.parent_asset_id IS DISTINCT FROM OLD.parent_asset_id
               OR NEW.root_asset_id IS DISTINCT FROM OLD.root_asset_id
               OR NEW.lineage_depth IS DISTINCT FROM OLD.lineage_depth THEN
                RAISE EXCEPTION 'content_assets lineage is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END $$;
        """
    )
    op.drop_index(
        "content_assets_one_derivative_idx",
        table_name="content_assets",
        postgresql_where=sa.text("parent_asset_id IS NOT NULL AND archived_at IS NULL"),
    )
    op.drop_constraint(op.f("ck_content_assets_source_iff_parent"), "content_assets", type_="check")
    op.drop_constraint("fk_content_assets_source_version", "content_assets", type_="foreignkey")
    op.drop_column("content_assets", "repurpose_instructions")
    op.drop_column("content_assets", "source_version_id")
