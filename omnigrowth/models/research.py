"""Sources & research: source_assets, research_packets, research_claims
(DATA_MODEL §4.1, §4.4, §4.5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from omnigrowth.core.enums import ActorType, ClaimLabel, SourceAssetStatus, SourceType
from omnigrowth.core.errors import ErrorCode
from omnigrowth.models._columns import EMPTY_JSON_OBJECT, TZ, CreatedAtMixin, fk, sha256_check
from omnigrowth.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin, pg_enum


class SourceAsset(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "source_assets"
    __table_args__ = (
        UniqueConstraint("brand_id", "checksum"),
        sha256_check("checksum"),
        CheckConstraint(
            "source_type NOT IN ('URL','YOUTUBE_URL') OR source_url IS NOT NULL",
            name="url_required",
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(pg_enum(SourceType), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=EMPTY_JSON_OBJECT
    )
    checksum: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[SourceAssetStatus] = mapped_column(
        pg_enum(SourceAssetStatus),
        nullable=False,
        server_default=SourceAssetStatus.PENDING.value,
    )
    error_code: Mapped[ErrorCode | None] = mapped_column(pg_enum(ErrorCode), nullable=True)
    actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType), nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)


Index(
    "source_assets_brand_created_idx",
    SourceAsset.brand_id,
    SourceAsset.created_at.desc(),
    postgresql_where=text("archived_at IS NULL"),
)


class ResearchPacket(UUIDPkMixin, CreatedAtMixin, ArchivableMixin, Base):
    __tablename__ = "research_packets"
    __table_args__ = (
        Index(
            "research_packets_campaign_current_uq",
            "campaign_id",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(fk("campaigns.id"), nullable=False)
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(fk("source_assets.id"), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(fk("agent_runs.id"), nullable=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="null")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResearchClaim(UUIDPkMixin, CreatedAtMixin, Base):
    """Immutable (UPDATE/DELETE-blocking trigger, if any, lives in the migration)."""

    __tablename__ = "research_claims"
    __table_args__ = (
        UniqueConstraint("research_packet_id", "ordinal"),
        sha256_check("citation_excerpt_hash"),
        CheckConstraint(
            "(citation_url IS NULL) = (citation_retrieved_at IS NULL) "
            "AND (citation_url IS NULL) = (citation_excerpt_hash IS NULL)",
            name="citation_all_or_none",
        ),
        Index(
            "research_claims_packet_url_idx",
            "research_packet_id",
            "citation_url",
            postgresql_where=text("citation_url IS NOT NULL"),
        ),
    )

    research_packet_id: Mapped[uuid.UUID] = mapped_column(fk("research_packets.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[ClaimLabel] = mapped_column(pg_enum(ClaimLabel), nullable=False)
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation_retrieved_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    citation_excerpt_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
