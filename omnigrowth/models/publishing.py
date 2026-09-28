"""Publishing: publication_jobs (transactional outbox, A3), publications
(DATA_MODEL §5.1, §5.2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from omnigrowth.core.enums import (
    ActorType,
    EvidenceClass,
    Platform,
    PublicationJobStatus,
    PublicationStatus,
    PublishMode,
)
from omnigrowth.core.errors import ErrorCode
from omnigrowth.models._columns import TZ, ZERO, fk, sha256_check
from omnigrowth.models.base import Base, TimestampMixin, UUIDPkMixin, pg_enum

# Statuses covered by the duplicate-post guard (ADR-0003; PROPOSED partial form).
IDEMPOTENCY_LIVE_WHERE = "status IN ('PENDING','CLAIMED','RETRY_WAIT','SUCCEEDED','UNCERTAIN')"


class PublicationJob(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "publication_jobs"
    __table_args__ = (
        sha256_check("idempotency_key"),
        CheckConstraint("attempt_count <= max_attempts", name="attempts_within_max"),
        CheckConstraint(
            "status <> 'CLAIMED' OR (claimed_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND claim_token IS NOT NULL)",
            name="claimed_has_lease",
        ),
        Index(
            "publication_jobs_idempotency_live_uq",
            "idempotency_key",
            unique=True,
            postgresql_where=text(IDEMPOTENCY_LIVE_WHERE),
        ),
        # Predicate must match the scheduler claim query exactly (DATA_MODEL §5.1).
        Index(
            "publication_jobs_claim_idx",
            "due_at",
            postgresql_where=text("status IN ('PENDING','RETRY_WAIT')"),
        ),
        Index(
            "publication_jobs_lease_idx",
            "lease_expires_at",
            postgresql_where=text("status = 'CLAIMED'"),
        ),
        Index(
            "publication_jobs_account_uncertain_idx",
            "platform_account_id",
            postgresql_where=text("status = 'UNCERTAIN'"),
        ),
        Index("publication_jobs_asset_idx", "content_asset_id"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    content_asset_id: Mapped[uuid.UUID] = mapped_column(fk("content_assets.id"), nullable=False)
    content_version_id: Mapped[uuid.UUID] = mapped_column(fk("content_versions.id"), nullable=False)
    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        fk("platform_accounts.id"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Written only by omnigrowth.core.state_machines (AGENTS.md §4).
    status: Mapped[PublicationJobStatus] = mapped_column(
        pg_enum(PublicationJobStatus),
        nullable=False,
        server_default=PublicationJobStatus.PENDING.value,
    )
    due_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    schedule_timezone: Mapped[str] = mapped_column(Text, nullable=False)
    publish_mode: Mapped[PublishMode | None] = mapped_column(pg_enum(PublishMode), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    claimed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    dispatch_started_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    last_error_code: Mapped[ErrorCode | None] = mapped_column(pg_enum(ErrorCode), nullable=True)
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType), nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)


class Publication(UUIDPkMixin, TimestampMixin, Base):
    """The external post. No hard delete; ``DELETED`` status records platform removal."""

    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("publication_job_id"),
        UniqueConstraint("platform_account_id", "external_post_id"),
        CheckConstraint(
            "status <> 'PUBLISHED' OR published_at IS NOT NULL", name="published_has_time"
        ),
        Index(
            "publications_account_uncertain_idx",
            "platform_account_id",
            postgresql_where=text("status = 'PUBLICATION_UNCERTAIN'"),
        ),
        Index("publications_asset_idx", "content_asset_id"),
        Index(
            "publications_manual_pending_idx",
            "brand_id",
            postgresql_where=text("status = 'MANUAL_PENDING'"),
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    publication_job_id: Mapped[uuid.UUID] = mapped_column(fk("publication_jobs.id"), nullable=False)
    content_asset_id: Mapped[uuid.UUID] = mapped_column(fk("content_assets.id"), nullable=False)
    content_version_id: Mapped[uuid.UUID] = mapped_column(fk("content_versions.id"), nullable=False)
    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        fk("platform_accounts.id"), nullable=False
    )
    platform: Mapped[Platform] = mapped_column(pg_enum(Platform), nullable=False)
    publish_mode: Mapped[PublishMode] = mapped_column(pg_enum(PublishMode), nullable=False)
    # A17: SIMULATED / SANDBOX / REAL, fixed at dispatch start (scheduler.evidence_class).
    evidence_class: Mapped[EvidenceClass] = mapped_column(pg_enum(EvidenceClass), nullable=False)
    external_post_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Written only by omnigrowth.core.state_machines (AGENTS.md §4).
    status: Mapped[PublicationStatus] = mapped_column(
        pg_enum(PublicationStatus),
        nullable=False,
        server_default=PublicationStatus.PUBLISHING.value,
    )
    scheduled_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    reconciled_by_actor_type: Mapped[ActorType | None] = mapped_column(
        pg_enum(ActorType), nullable=True
    )
    reconciled_by_actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)


Index(
    "publications_brand_published_idx",
    Publication.brand_id,
    Publication.published_at.desc(),
    postgresql_where=text("status = 'PUBLISHED'"),
)
