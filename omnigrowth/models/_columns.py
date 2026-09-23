"""Small column/constraint helpers shared by the domain model modules (DATA_MODEL §1).

Kept separate from ``base.py`` (primary-owned) so domain modules stay terse.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, TextClause, func, text
from sqlalchemy.orm import Mapped, mapped_column

# timestamptz (DATA_MODEL §1: every timestamp is timezone-aware UTC).
TZ = DateTime(timezone=True)

# Server defaults for empty containers (DATA_MODEL `'{}'` / `'[]'`).
EMPTY_ARRAY: TextClause = text("'{}'")
EMPTY_JSON_OBJECT: TextClause = text("'{}'::jsonb")
EMPTY_JSON_ARRAY: TextClause = text("'[]'::jsonb")
ZERO: TextClause = text("0")


def fk(target: str) -> ForeignKey:
    """FK with DATA_MODEL §1 default action: ``ON DELETE RESTRICT``."""
    return ForeignKey(target, ondelete="RESTRICT")


def sha256_check(column: str, name: str | None = None) -> CheckConstraint:
    """``CHECK (col ~ '^[0-9a-f]{64}$')`` (DATA_MODEL §1 Hashes). NULL passes (nullable cols)."""
    return CheckConstraint(f"{column} ~ '^[0-9a-f]{{64}}$'", name=name or f"{column}_sha256")


class CreatedAtMixin:
    """``created_at`` only — for immutable / append-only tables (no ``updated_at``)."""

    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), nullable=False)
