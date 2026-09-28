"""Integration (n8n) API schemas — API_PLAN §2.18, §3.12; Phase 12.

Secrets appear ONLY in ``*CreatedRead`` (creation and rotation), once. Reads carry a short
fingerprint so people can tell which secret an n8n credential holds.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from omnigrowth.core.enums import (
    IntegrationEventType,
    IntegrationOperation,
    SourceType,
    WebhookDeliveryStatus,
)
from omnigrowth.schemas.sources import JobRead


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ------------------------------------------------------------------ inbound endpoints
class WebhookEndpointCreate(_Request):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$", description="URL slug of the endpoint.")
    description: str | None = Field(default=None, max_length=500)
    brand_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    allowed_operations: list[IntegrationOperation] = Field(min_length=1, max_length=10)


class WebhookEndpointRead(BaseModel):
    id: uuid.UUID
    key: str
    description: str | None
    brand_ids: list[uuid.UUID]
    allowed_operations: list[IntegrationOperation]
    inbound_path: str
    secret_version: int
    secret_fingerprint: str
    secret_rotated_at: datetime | None
    previous_secret_valid_until: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_by: str
    created_at: datetime


class WebhookEndpointCreatedRead(WebhookEndpointRead):
    secret: str = Field(description="Shown once. Store it in n8n credentials, never in a workflow.")


# ------------------------------------------------------------------ outbound subscriptions
class WebhookSubscriptionCreate(_Request):
    target_url: HttpUrl
    description: str | None = Field(default=None, max_length=500)
    brand_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    event_types: list[IntegrationEventType] = Field(min_length=1, max_length=10)


class WebhookSubscriptionRead(BaseModel):
    id: uuid.UUID
    target_url: str
    description: str | None
    brand_ids: list[uuid.UUID]
    event_types: list[IntegrationEventType]
    secret_version: int
    secret_fingerprint: str
    secret_rotated_at: datetime | None
    previous_secret_valid_until: datetime | None
    revoked_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    created_by: str
    created_at: datetime


class WebhookSubscriptionCreatedRead(WebhookSubscriptionRead):
    secret: str = Field(description="Shown once. Used by the receiver to verify signatures.")


class WebhookDeliveryRead(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    event_type: IntegrationEventType
    brand_id: uuid.UUID
    subscription_id: uuid.UUID
    status: WebhookDeliveryStatus
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    last_status_code: int | None
    last_error: str | None
    delivered_at: datetime | None
    occurred_at: datetime
    created_at: datetime


# ------------------------------------------------------------------ inbound envelope
class WebhookInboundEnvelope(_Request):
    """Body of ``POST /webhooks/inbound/{endpoint_key}`` (HMAC-signed raw bytes)."""

    operation: IntegrationOperation
    payload: dict[str, Any]


class TextIngestPayload(_Request):
    brand_id: uuid.UUID
    content: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=500)
    source_type: SourceType = SourceType.TEXT


class UrlIngestPayload(_Request):
    brand_id: uuid.UUID
    url: str = Field(min_length=8, max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    source_type: SourceType = SourceType.URL


class CampaignGeneratePayload(_Request):
    campaign_id: uuid.UUID


class WebhookAcceptedRead(BaseModel):
    delivery_id: str
    operation: IntegrationOperation
    replayed: bool = False
    job: JobRead | None = None
    result_ref: dict[str, Any] | None = None
