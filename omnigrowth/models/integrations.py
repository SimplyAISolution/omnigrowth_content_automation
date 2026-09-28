"""Phase 12 integration tables (n8n): inbound endpoints, outbound subscriptions, the event
outbox and per-subscription deliveries.

No secret is stored: every signing secret is derived from the server key
(``OMNIGROWTH_WEBHOOK_KEY``) and the row's id + ``secret_version`` (security/webhooks.py), so a
database dump reveals none. Events are inserted by database triggers in the same transaction
as the state change that causes them (migration c12a0d5e7f31), so no code path can skip one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, SmallInteger, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnigrowth.core.enums import IntegrationEventType, IntegrationOperation, WebhookDeliveryStatus
from omnigrowth.models._columns import EMPTY_JSON_OBJECT, TZ, ZERO, CreatedAtMixin, fk
from omnigrowth.models.base import Base, TimestampMixin, UUIDPkMixin


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _subset(column: str, values: list[str]) -> str:
    return f"{column} <@ ARRAY[{', '.join(repr(v) for v in values)}]::text[]"


OPERATIONS = [o.value for o in IntegrationOperation]
EVENT_TYPES = [e.value for e in IntegrationEventType]
DELIVERY_STATES = [s.value for s in WebhookDeliveryStatus]


class _SecretMixin:
    """Current secret version; the previous one stays valid until the grace period ends."""

    secret_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    previous_secret_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_secret_valid_until: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    secret_rotated_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)


class WebhookEndpoint(_SecretMixin, UUIDPkMixin, TimestampMixin, Base):
    """An inbound integration principal (the INTEGRATION role): HMAC-authenticated, limited to
    ``brand_ids`` and ``allowed_operations``. It can never approve, schedule, publish, or touch
    accounts, credentials or settings."""

    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        UniqueConstraint("key"),
        CheckConstraint("key ~ '^[a-z0-9][a-z0-9-]{2,63}$'", name="key_slug"),
        CheckConstraint("cardinality(brand_ids) >= 1", name="brand_scope_required"),
        CheckConstraint("cardinality(allowed_operations) >= 1", name="operations_required"),
        CheckConstraint(_subset("allowed_operations", OPERATIONS), name="operations_known"),
    )

    key: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    allowed_operations: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)


class WebhookSubscription(_SecretMixin, UUIDPkMixin, TimestampMixin, Base):
    """An outbound target (for example an n8n Webhook node) for some event types and brands."""

    __tablename__ = "webhook_subscriptions"
    __table_args__ = (
        CheckConstraint("cardinality(brand_ids) >= 1", name="brand_scope_required"),
        CheckConstraint("cardinality(event_types) >= 1", name="event_types_required"),
        CheckConstraint(_subset("event_types", EVENT_TYPES), name="event_types_known"),
    )

    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)


class IntegrationEvent(CreatedAtMixin, Base):
    """Append-only event outbox. ``id`` is the stable event id receivers deduplicate on."""

    __tablename__ = "integration_events"
    __table_args__ = (
        CheckConstraint(_in("event_type", EVENT_TYPES), name="event_type_known"),
        Index(
            "integration_events_unfanned_idx",
            "occurred_at",
            postgresql_where=text("fanned_out_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    brand_id: Mapped[uuid.UUID] = mapped_column(fk("brands.id"), nullable=False)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSON_OBJECT
    )
    occurred_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))
    fanned_out_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)


class WebhookDelivery(UUIDPkMixin, TimestampMixin, Base):
    """One event to one subscription, retried with backoff until 2xx or exhausted.

    Operational bookkeeping like ``idempotency_keys.state`` (text + CHECK), not a domain
    status: it has no state machine and never gates approval or publication."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "subscription_id"),
        CheckConstraint(_in("state", DELIVERY_STATES), name="state_known"),
        CheckConstraint("attempt_count >= 0 AND attempt_count <= max_attempts", name="attempts"),
        Index(
            "webhook_deliveries_due_idx",
            "next_attempt_at",
            postgresql_where=text("state IN ('PENDING', 'DELIVERING')"),
        ),
        Index("webhook_deliveries_subscription_idx", "subscription_id", "state"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(fk("integration_events.id"), nullable=False)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        fk("webhook_subscriptions.id"), nullable=False
    )
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{WebhookDeliveryStatus.PENDING.value}'")
    )
    attempt_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=ZERO)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
