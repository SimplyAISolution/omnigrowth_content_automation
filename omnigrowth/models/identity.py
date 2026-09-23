"""Identity & audit tables: users, api_tokens, audit_events (DATA_MODEL §3.1, §3.2, §3.9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Index, Text, UniqueConstraint, func, true
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from omnigrowth.core.enums import ActorType, UserRole
from omnigrowth.models._columns import (
    EMPTY_ARRAY,
    EMPTY_JSON_OBJECT,
    TZ,
    CreatedAtMixin,
    fk,
    sha256_check,
)
from omnigrowth.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin, pg_enum


class User(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)

    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole), nullable=False, server_default=UserRole.VIEWER.value
    )
    # argon2id encoded hash (ADR-0006); never returned by the API or logged.
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class ApiToken(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "api_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        sha256_check("token_hash"),
        Index("api_tokens_user_id_idx", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(fk("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    expires_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)


class AuditEvent(UUIDPkMixin, Base):
    """Append-only. UPDATE/DELETE-blocking trigger is created in the Alembic migration."""

    __tablename__ = "audit_events"

    occurred_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=func.now())
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType), nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(fk("brands.id"), nullable=True)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # polymorphic, no FK
    action: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # ``metadata`` is reserved on declarative classes; column name stays ``metadata``.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=EMPTY_JSON_OBJECT
    )


Index(
    "audit_events_entity_timeline_idx",
    AuditEvent.entity_type,
    AuditEvent.entity_id,
    AuditEvent.occurred_at,
)
Index("audit_events_correlation_idx", AuditEvent.correlation_id)
Index("audit_events_brand_time_idx", AuditEvent.brand_id, AuditEvent.occurred_at.desc())
Index(
    "audit_events_actor_time_idx",
    AuditEvent.actor_type,
    AuditEvent.actor_id,
    AuditEvent.occurred_at.desc(),
)
