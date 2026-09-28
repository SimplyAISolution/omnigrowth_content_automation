"""Approval, platform-account and publication API schemas (API_PLAN §2.8, §2.9, §2.12)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from omnigrowth.core.enums import (
    ActorType,
    ApprovalActionType,
    ContentAssetStatus,
    CredentialKind,
    EvidenceClass,
    Platform,
    PlatformAccountType,
    PublicationJobStatus,
    PublicationStatus,
    PublishMode,
)
from omnigrowth.core.errors import ErrorCode
from omnigrowth.schemas.brand import Timezone
from omnigrowth.schemas.content import ContentAssetRead, ContentVersionRead
from omnigrowth.schemas.sources import JobRead


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ platform accounts
class PlatformAccountCreate(_Request):
    """``live_enabled`` is deliberately absent: accounts are always created non-live (A6)."""

    brand_id: uuid.UUID
    platform: Platform
    account_type: PlatformAccountType
    handle: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    timezone: Timezone | None = None
    # REDDIT subreddit profiles / MASTODON instance limits (content/platform_profiles.py).
    settings: dict[str, Any] | None = None


class PlatformAccountUpdate(_Request):
    """Omitted fields are kept; ``settings`` replaces the whole settings object."""

    handle: str | None = Field(default=None, min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    timezone: Timezone | None = None
    settings: dict[str, Any] | None = None
    # A17: live posts from a sandbox account are SANDBOX evidence. Refused while live.
    sandbox: bool | None = None


class PlatformAccountLiveRequest(_Request):
    """Turn live publishing on or off for one account (OWNER, audited, A6 gate #2).

    Enabling requires ``confirmation`` to be exactly ``ENABLE LIVE PUBLISHING FOR <PLATFORM>``
    (AGENTS.md §3), a VERIFIED connector, configured settings and a stored credential."""

    enabled: bool
    confirmation: str | None = Field(default=None, max_length=100)


class CredentialCreate(_Request):
    """Write-only: the secret is encrypted at rest and never returned (A17)."""

    kind: CredentialKind
    secret: SecretStr = Field(min_length=8, max_length=4096)
    scopes: list[str] = Field(default_factory=list, max_length=20)
    expires_at: datetime | None = None

    @field_validator("secret")
    @classmethod
    def _one_line(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw != raw.strip() or any(ch.isspace() for ch in raw):
            raise ValueError("the secret must be a single token without spaces or line breaks")
        return value

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, value: list[str]) -> list[str]:
        cleaned = sorted({s.strip() for s in value if s.strip()})
        if any(len(s) > 64 or " " in s for s in cleaned):
            raise ValueError("invalid scope")
        return cleaned


class CredentialRead(_Read):
    """Metadata only. There is deliberately no field that could carry the secret."""

    id: uuid.UUID
    platform_account_id: uuid.UUID
    kind: CredentialKind
    scopes: list[str]
    fingerprint: str | None
    kek_id: str
    expires_at: datetime | None
    revoked_at: datetime | None
    created_by: str | None
    revoked_by: str | None
    created_at: datetime


class PlatformAccountRead(_Read):
    id: uuid.UUID
    brand_id: uuid.UUID
    platform: Platform
    account_type: PlatformAccountType
    handle: str
    display_name: str | None
    timezone: str | None
    live_enabled: bool
    sandbox: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)
    effective_publisher: PublishMode | None = None
    has_open_uncertain: bool = False
    # Phase 13: whether a VERIFIED live connector exists, a credential is stored, and what
    # would stop a live post right now (empty = nothing; the dispatcher re-checks anyway).
    live_connector_available: bool = False
    has_credential: bool = False
    live_blockers: list[str] = Field(default_factory=list)
    created_at: datetime


# ------------------------------------------------------------------ approvals (§37)
class ApprovalEdit(_Request):
    """The new content for an EDIT (§37); omitted fields keep the reviewed version's."""

    body: str = Field(min_length=1, max_length=20_000)
    hook: str | None = Field(default=None, max_length=600)
    cta: str | None = Field(default=None, max_length=400)
    title: str | None = Field(default=None, max_length=300)
    hashtags: list[str] | None = Field(default=None, max_length=10)
    platform_fields: dict[str, Any] | None = None  # see ContentVersionCreate


class ApprovalActionRequest(_Request):
    action: ApprovalActionType
    content_version_id: uuid.UUID
    comment: str | None = Field(default=None, max_length=4000)
    platform_account_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    edit: ApprovalEdit | None = None

    @model_validator(mode="after")
    def _required_fields(self) -> ApprovalActionRequest:
        a = self.action
        if a is ApprovalActionType.EDIT and self.edit is None:
            raise ValueError("EDIT requires edit")
        if a in (ApprovalActionType.REJECT, ApprovalActionType.REQUEST_REVISION) and not (
            self.comment and self.comment.strip()
        ):
            raise ValueError(f"{a.value} requires a comment")
        if a in (ApprovalActionType.SCHEDULE, ApprovalActionType.PUBLISH_NOW) and (
            self.platform_account_id is None
        ):
            raise ValueError(f"{a.value} requires platform_account_id")
        if a is ApprovalActionType.SCHEDULE:
            if self.scheduled_at is None:
                raise ValueError("SCHEDULE requires scheduled_at")
            if self.scheduled_at.tzinfo is None:
                raise ValueError("scheduled_at must include a timezone offset")
        return self


class ApprovalActionRead(_Read):
    id: uuid.UUID
    content_asset_id: uuid.UUID
    content_version_id: uuid.UUID
    resulting_version_id: uuid.UUID | None
    action: ApprovalActionType
    from_status: ContentAssetStatus
    to_status: ContentAssetStatus
    scheduled_for: datetime | None
    platform_account_id: uuid.UUID | None
    comment: str | None
    actor_type: ActorType
    actor_id: str
    correlation_id: str
    created_at: datetime


# ------------------------------------------------------------------ publications
class PublicationJobRead(_Read):
    id: uuid.UUID
    brand_id: uuid.UUID
    content_asset_id: uuid.UUID
    content_version_id: uuid.UUID
    platform_account_id: uuid.UUID
    idempotency_key: str
    status: PublicationJobStatus
    due_at: datetime
    schedule_timezone: str
    publish_mode: PublishMode | None
    attempt_count: int
    max_attempts: int
    dispatch_started_at: datetime | None
    last_error_code: ErrorCode | None
    last_error: dict[str, Any] | None
    completed_at: datetime | None
    created_at: datetime


class PublicationRead(_Read):
    id: uuid.UUID
    brand_id: uuid.UUID
    publication_job_id: uuid.UUID
    content_asset_id: uuid.UUID
    content_version_id: uuid.UUID
    platform_account_id: uuid.UUID
    platform: Platform
    publish_mode: PublishMode
    evidence_class: EvidenceClass
    external_post_id: str | None
    canonical_url: str | None
    status: PublicationStatus
    scheduled_at: datetime
    published_at: datetime | None
    error: dict[str, Any] | None
    reconciled_at: datetime | None


class PublicationPreviewRead(BaseModel):
    """What the dispatcher would send for a job, before it is sent (no secrets)."""

    publication_job_id: uuid.UUID
    brand_id: uuid.UUID
    platform: Platform
    platform_account_id: uuid.UUID
    account_handle: str
    due_at: datetime
    publish_mode: PublishMode
    evidence_class: EvidenceClass
    simulated: bool
    content_version_id: uuid.UUID
    details: dict[str, Any]
    blockers: list[str]


class PublicationLookupRead(BaseModel):
    """Result of searching the platform for an uncertain post (never re-posts).

    FOUND reconciles it (J12/P5); PARTIAL and NOT_FOUND change nothing: absence among the
    posts searched is not proof, so a person still decides."""

    status: str
    searched: int
    found_post_ids: list[str]
    detail: str
    publication: PublicationRead


class ApprovalActionResultRead(BaseModel):
    asset: ContentAssetRead
    publication_job: PublicationJobRead | None = None
    job: JobRead | None = None  # QA_RUN after EDIT, ASSET_GENERATION after REGENERATE
    version: ContentVersionRead | None = None  # the version an EDIT created


class CancelRequest(_Request):
    reason: str | None = Field(default=None, max_length=2000)


class ReconcileMethod(StrEnum):
    MANUAL_CONFIRM_PUBLISHED = "MANUAL_CONFIRM_PUBLISHED"
    MANUAL_CONFIRM_NOT_PUBLISHED = "MANUAL_CONFIRM_NOT_PUBLISHED"


class PublicationReconcileRequest(_Request):
    method: ReconcileMethod
    canonical_url: str | None = Field(default=None, max_length=2048)
    external_post_id: str | None = Field(default=None, max_length=500)
    published_at: datetime | None = None  # when the post actually went out, if known
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _required_fields(self) -> PublicationReconcileRequest:
        if self.method is ReconcileMethod.MANUAL_CONFIRM_PUBLISHED and not self.canonical_url:
            raise ValueError("MANUAL_CONFIRM_PUBLISHED requires canonical_url")
        if self.method is ReconcileMethod.MANUAL_CONFIRM_NOT_PUBLISHED and not (
            self.note and self.note.strip()
        ):
            raise ValueError("MANUAL_CONFIRM_NOT_PUBLISHED requires a note")
        return self
