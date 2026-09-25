"""Approval, platform-account and publication API schemas (API_PLAN §2.8, §2.9, §2.12)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omnigrowth.core.enums import (
    ActorType,
    ApprovalActionType,
    ContentAssetStatus,
    Platform,
    PlatformAccountType,
    PublicationJobStatus,
    PublicationStatus,
    PublishMode,
)
from omnigrowth.core.errors import ErrorCode
from omnigrowth.schemas.brand import Timezone
from omnigrowth.schemas.content import ContentAssetRead


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


class PlatformAccountRead(_Read):
    id: uuid.UUID
    brand_id: uuid.UUID
    platform: Platform
    account_type: PlatformAccountType
    handle: str
    display_name: str | None
    timezone: str | None
    live_enabled: bool
    effective_publisher: PublishMode | None = None
    has_open_uncertain: bool = False
    created_at: datetime


# ------------------------------------------------------------------ approvals (§37)
class ApprovalActionRequest(_Request):
    action: ApprovalActionType
    content_version_id: uuid.UUID
    comment: str | None = Field(default=None, max_length=4000)
    platform_account_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None

    @model_validator(mode="after")
    def _required_fields(self) -> ApprovalActionRequest:
        a = self.action
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
    external_post_id: str | None
    canonical_url: str | None
    status: PublicationStatus
    scheduled_at: datetime
    published_at: datetime | None
    error: dict[str, Any] | None
    reconciled_at: datetime | None


class ApprovalActionResultRead(BaseModel):
    asset: ContentAssetRead
    publication_job: PublicationJobRead | None = None


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
