"""Brand domain: brands, brand_profiles, audiences, offers, platform_accounts,
platform_credentials, app_settings (DATA_MODEL §3.3-§3.8, §6a.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from omnigrowth.core.enums import (
    ActorType,
    CredentialKind,
    Platform,
    PlatformAccountType,
    RoutingProfile,
)
from omnigrowth.models._columns import (
    EMPTY_ARRAY,
    EMPTY_JSON_ARRAY,
    EMPTY_JSON_OBJECT,
    TZ,
    CreatedAtMixin,
    fk,
)
from omnigrowth.models.base import ArchivableMixin, Base, TimestampMixin, UUIDPkMixin, pg_enum


class Brand(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("slug"),)

    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="UTC")
    default_routing_profile: Mapped[RoutingProfile] = mapped_column(
        pg_enum(RoutingProfile),
        nullable=False,
        server_default=RoutingProfile.LOCAL_ONLY.value,
    )
    auto_approve_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )


class BrandProfile(UUIDPkMixin, CreatedAtMixin, Base):
    """Append-only versions of the brand profile (§5)."""

    __tablename__ = "brand_profiles"
    __table_args__ = (
        UniqueConstraint("brand_id", "version_no"),
        CheckConstraint("version_no > 0", name="version_no_positive"),
        Index(
            "brand_profiles_one_active_uq",
            "brand_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    company_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    niche: Mapped[str | None] = mapped_column(Text, nullable=True)
    mission: Mapped[str | None] = mapped_column(Text, nullable=True)
    products: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_ARRAY
    )
    services: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_ARRAY
    )
    differentiators: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_ARRAY
    )
    voice: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_OBJECT
    )
    avoid_list: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    banned_phrases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    vocabulary: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    content_pillars: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType), nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)


class Audience(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "audiences"
    __table_args__ = (
        UniqueConstraint("brand_id", "name"),
        CheckConstraint("sophistication_level BETWEEN 1 AND 5", name="sophistication_level_range"),
        Index(
            "audiences_one_primary_uq",
            "brand_id",
            unique=True,
            postgresql_where=text("is_primary AND archived_at IS NULL"),
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pains: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    goals: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    objections: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    common_questions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    vocabulary: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    sophistication_level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Written only by accepting a recommendation (§20/§21).
    observed_interests: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_OBJECT
    )


class Offer(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "offers"
    __table_args__ = (
        CheckConstraint("(price IS NULL) = (currency IS NULL)", name="price_currency_pair"),
        Index("offers_brand_active_idx", "brand_id", postgresql_where=text("archived_at IS NULL")),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    audience_id: Mapped[uuid.UUID | None] = mapped_column(fk("audiences.id"), nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    cta: Mapped[str | None] = mapped_column(Text, nullable=True)
    funnel_destination_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    proof: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=EMPTY_JSON_ARRAY)
    objections: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )


class PlatformAccount(UUIDPkMixin, TimestampMixin, ArchivableMixin, Base):
    __tablename__ = "platform_accounts"
    __table_args__ = (
        UniqueConstraint("brand_id", "platform", "handle"),
        UniqueConstraint("platform", "external_account_id"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    platform: Mapped[Platform] = mapped_column(pg_enum(Platform), nullable=False)
    account_type: Mapped[PlatformAccountType] = mapped_column(
        pg_enum(PlatformAccountType), nullable=False
    )
    handle: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str | None] = mapped_column(Text, nullable=True)
    live_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # A17: a sandbox account (test instance / test account) makes LIVE posts that are not
    # real audience evidence (publications get evidence_class SANDBOX). Changing it is audited.
    sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_OBJECT
    )


class PlatformCredential(UUIDPkMixin, TimestampMixin, Base):
    """Envelope-encrypted secret. Never serialized by any API schema."""

    __tablename__ = "platform_credentials"
    __table_args__ = (
        CheckConstraint("octet_length(nonce) = 12", name="nonce_length"),
        Index(
            "platform_credentials_active_uq",
            "platform_account_id",
            "kind",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "platform_credentials_expiry_idx",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        fk("platform_accounts.id"), nullable=False
    )
    kind: Mapped[CredentialKind] = mapped_column(pg_enum(CredentialKind), nullable=False)
    enc_alg: Mapped[str] = mapped_column(Text, nullable=False, server_default="AES-256-GCM")
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kek_id: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    expires_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    # Non-secret identifiers for humans (vault.fingerprint) and who stored / revoked it.
    fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppSetting(UUIDPkMixin, Base):
    """DB-backed tunables / routing overrides (§6a.3). ENV-sourced settings never live here."""

    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("brand_id", "key", postgresql_nulls_not_distinct=True),)

    brand_id: Mapped[uuid.UUID | None] = mapped_column(fk("brands.id"), nullable=True)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[uuid.UUID] = mapped_column(fk("users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )
