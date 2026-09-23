"""AI runs, prompts & vectors: prompt_versions, agent_runs, model_runs, embeddings
(DATA_MODEL §6.1-§6.4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from omnigrowth.core.enums import (
    AgentRunStatus,
    EmbeddingField,
    EmbeddingOwnerType,
    ModelLocality,
    ModelRole,
    ModelRunStatus,
    PrivacyClass,
    RoutingProfile,
)
from omnigrowth.core.errors import ErrorCode
from omnigrowth.models._columns import TZ, ZERO, CreatedAtMixin, fk, sha256_check
from omnigrowth.models.base import Base, UUIDPkMixin, pg_enum


class PromptVersion(UUIDPkMixin, CreatedAtMixin, Base):
    """Registry of prompt files; immutable once written."""

    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("domain", "name", "version"),
        sha256_check("content_hash"),
    )

    domain: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    front_matter: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class AgentRun(UUIDPkMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("agent_runs_campaign_started_idx", "campaign_id", "started_at"),
        Index("agent_runs_correlation_idx", "correlation_id"),
        Index(
            "agent_runs_parent_idx",
            "parent_agent_run_id",
            postgresql_where=text("parent_agent_run_id IS NOT NULL"),
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(fk("campaigns.id"), nullable=True)
    parent_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("agent_runs.id"), nullable=True
    )
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("prompt_versions.id"), nullable=True
    )
    brand_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("brand_profiles.id"), nullable=True
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        pg_enum(AgentRunStatus), nullable=False, server_default=AgentRunStatus.RUNNING.value
    )
    input_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[ErrorCode | None] = mapped_column(pg_enum(ErrorCode), nullable=True)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    rq_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)


class ModelRun(UUIDPkMixin, CreatedAtMixin, Base):
    """One row per provider call (incl. repair attempts). Immutable."""

    __tablename__ = "model_runs"
    __table_args__ = (
        CheckConstraint("attempt_no BETWEEN 0 AND 2", name="attempt_no_range"),
        CheckConstraint("(status = 'FAILED') = (error_code IS NOT NULL)", name="failed_has_error"),
        CheckConstraint(
            "error_code IS DISTINCT FROM 'LLM_OUTPUT_INVALID' OR raw_response IS NOT NULL",
            name="invalid_output_has_raw",
        ),
        # §52 backstop: PRIVATE payloads never recorded against a REMOTE model.
        CheckConstraint(
            "NOT (privacy_class = 'PRIVATE' AND locality = 'REMOTE')", name="private_not_remote"
        ),
        sha256_check("input_hash"),
        Index(
            "model_runs_campaign_cost_idx",
            "campaign_id",
            "created_at",
            postgresql_include=["estimated_cost_usd"],
        ),
        Index("model_runs_agent_run_idx", "agent_run_id"),
    )

    brand_id: Mapped[uuid.UUID | None] = mapped_column(fk("brands.id"), nullable=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(fk("campaigns.id"), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("agent_runs.id"), nullable=True)
    repair_of_model_run_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("model_runs.id"), nullable=True
    )
    attempt_no: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=ZERO)
    task: Mapped[str] = mapped_column(Text, nullable=False)
    model_role: Mapped[ModelRole] = mapped_column(pg_enum(ModelRole), nullable=False)
    routing_profile: Mapped[RoutingProfile] = mapped_column(pg_enum(RoutingProfile), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    locality: Mapped[ModelLocality] = mapped_column(pg_enum(ModelLocality), nullable=False)
    privacy_class: Mapped[PrivacyClass] = mapped_column(pg_enum(PrivacyClass), nullable=False)
    structured_output_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        fk("prompt_versions.id"), nullable=True
    )
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[ModelRunStatus] = mapped_column(pg_enum(ModelRunStatus), nullable=False)
    error_code: Mapped[ErrorCode | None] = mapped_column(pg_enum(ErrorCode), nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)


Index("model_runs_created_idx", ModelRun.created_at.desc())
Index(
    "model_runs_failed_idx",
    ModelRun.error_code,
    ModelRun.created_at.desc(),
    postgresql_where=text("status = 'FAILED'"),
)


class Embedding(UUIDPkMixin, CreatedAtMixin, Base):
    """pgvector storage (A4), dimensionless ``vector`` column.

    Per-model partial expression HNSW indexes (``embeddings_hnsw_<model_slug>``) are NOT
    declared here: they are created per registered embedding model by Alembic revisions.
    Hard-deletable derived data.
    """

    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint(
            "owner_type",
            "owner_id",
            "field",
            "chunk_index",
            "embedding_model",
            name="uq_embeddings_owner_field_chunk_model",
        ),
        CheckConstraint("vector_dims(embedding) = dim", name="dim_matches"),
        sha256_check("content_hash"),
        Index("embeddings_model_idx", "embedding_model"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    owner_type: Mapped[EmbeddingOwnerType] = mapped_column(
        pg_enum(EmbeddingOwnerType), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # polymorphic, no FK
    field: Mapped[EmbeddingField] = mapped_column(
        pg_enum(EmbeddingField), nullable=False, server_default=EmbeddingField.FULL.value
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    dim: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(), nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("model_runs.id"), nullable=True)
