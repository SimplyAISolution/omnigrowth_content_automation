"""Platform plumbing: jobs, idempotency_keys (DATA_MODEL §6a.1, §6a.2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, SmallInteger, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from omnigrowth.core.enums import ActorType, JobKind, JobStatus
from omnigrowth.core.errors import ErrorCode
from omnigrowth.models._columns import TZ, ZERO, CreatedAtMixin, fk, sha256_check
from omnigrowth.models.base import Base, UUIDPkMixin, pg_enum


class Job(UUIDPkMixin, Base):
    """Durable async-job record; ``id`` is also the RQ job id."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "jobs_active_idx",
            "status",
            "queued_at",
            postgresql_where=text("status IN ('QUEUED','RUNNING')"),
        ),
    )

    brand_id: Mapped[uuid.UUID | None] = mapped_column(fk("brands.id"), nullable=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(fk("campaigns.id"), nullable=True)
    kind: Mapped[JobKind] = mapped_column(pg_enum(JobKind), nullable=False)
    # Written only by the jobs status helper in omnigrowth.core.state_machines.
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus), nullable=False, server_default=JobStatus.QUEUED.value
    )
    subject_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    requested_by_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType), nullable=False)
    requested_by_id: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=ZERO)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[ErrorCode | None] = mapped_column(pg_enum(ErrorCode), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)


Index("jobs_subject_idx", Job.subject_type, Job.subject_id, Job.queued_at.desc())


class IdempotencyKey(UUIDPkMixin, CreatedAtMixin, Base):
    """Request-level Idempotency-Key store (distinct from publication_jobs.idempotency_key)."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "principal_type",
            "principal_id",
            "method",
            "route",
            "key",
            name="uq_idempotency_keys_principal_method_route_key",
        ),
        CheckConstraint(
            "principal_type IN ('USER','WEBHOOK_ENDPOINT')", name="principal_type_allowed"
        ),
        CheckConstraint("char_length(key) BETWEEN 8 AND 128", name="key_length"),
        CheckConstraint("state IN ('IN_PROGRESS','COMPLETED')", name="state_allowed"),
        CheckConstraint(
            "state <> 'COMPLETED' OR response_status IS NOT NULL", name="completed_has_status"
        ),
        sha256_check("request_hash"),
        Index("idempotency_keys_expires_idx", "expires_at"),
    )

    principal_type: Mapped[str] = mapped_column(Text, nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # polymorphic, no FK
    method: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="IN_PROGRESS")
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    response_body: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
