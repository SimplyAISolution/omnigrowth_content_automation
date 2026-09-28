"""Outbound webhook delivery (Phase 12): at-least-once, bounded, never blocking the core flow.

1. **Fan-out.** Database triggers write ``integration_events`` in the same transaction as the
   state change (approval requested, publication failed/uncertain). ``fan_out`` turns each new
   event into one ``webhook_deliveries`` row per active subscription whose event types and
   brands match and that existed when the event happened (unique per event + subscription).
2. **Delivery.** ``claim_due`` leases due rows (``FOR UPDATE SKIP LOCKED``; a crashed sender's
   lease expires and the row is retried) and counts the attempt at claim time, so attempts are
   bounded even across crashes. ``deliver`` POSTs the signed payload through the SSRF-guarded
   ``safe_post`` with a timeout: 2xx = SUCCEEDED; 410 Gone = FAILED at once; anything else is
   retried with exponential backoff + jitter until ``max_attempts``, then FAILED (inspectable
   and redeliverable by an owner).
3. **Idempotency.** The payload (and its ``id`` = event id) is identical on every attempt;
   receivers deduplicate on ``X-OmniGrowth-Event-ID``. Duplicates are possible by design
   (a 2xx lost in transit is retried).

This runs in its own scheduler loop; nothing in the approval or publication path waits for it,
and a receiver outage only grows the backlog. Receiving an event changes nothing in OmniGrowth.
"""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import ContentAssetStatus, IntegrationEventType, WebhookDeliveryStatus
from omnigrowth.core.errors import OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.settings import get_settings
from omnigrowth.models import ContentAsset
from omnigrowth.models.integrations import (
    IntegrationEvent,
    WebhookDelivery,
    WebhookSubscription,
)
from omnigrowth.security import webhooks as sig
from omnigrowth.security.http import safe_post
from omnigrowth.services.integrations import active_secrets, target_allowlist

log = logging.getLogger(__name__)

API_VERSION = "2026-09"
LEASE = timedelta(seconds=90)
# Delay before attempt n+1 (seconds); the last value repeats. About 22 h across 8 attempts.
BACKOFF_S = (30, 120, 600, 1800, 3600, 7200, 14400, 28800)
BATCH = 20
FAN_OUT_BATCH = 200
FAN_OUT_PASSES = 10

Sender = Callable[..., Awaitable[int]]


def backoff(attempt: int, rng: random.Random | None = None) -> timedelta:
    """Delay after failed attempt number ``attempt`` (1-based), with ±10 % jitter."""
    base = BACKOFF_S[min(max(attempt, 1), len(BACKOFF_S)) - 1]
    jitter = (rng or random.Random()).uniform(-0.1, 0.1)  # noqa: S311 - scheduling jitter
    return timedelta(seconds=base * (1 + jitter))


def links(event: IntegrationEvent) -> dict[str, str]:
    base = get_settings().app_url.rstrip("/")
    data = event.data or {}
    out: dict[str, str] = {}
    if data.get("content_asset_id"):
        out["content"] = f"{base}/content/{data['content_asset_id']}"
    if event.event_type in (
        IntegrationEventType.PUBLICATION_FAILED.value,
        IntegrationEventType.PUBLICATION_UNCERTAIN.value,
    ):
        out["publishing"] = f"{base}/publications"
    if event.event_type == IntegrationEventType.CONTENT_APPROVAL_REQUESTED.value:
        out["approvals"] = f"{base}/approvals"
    return out


def payload(event: IntegrationEvent) -> bytes:
    """Minimal, deterministic body: ids, labels, timestamps, brand scope and links only."""
    body = {
        "id": str(event.id),
        "type": event.event_type,
        "api_version": API_VERSION,
        "occurred_at": event.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "brand_id": str(event.brand_id),
        "data": event.data or {},
        "links": links(event),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


# ------------------------------------------------------------------ fan-out
async def _still_relevant(session: AsyncSession, event: IntegrationEvent) -> bool:
    """``content.approval_requested`` only while the content still waits for a person.

    With automatic approval the asset passes AWAITING_APPROVAL -> APPROVED in one transaction;
    the trigger still records the event, and it is dropped here instead of telling reviewers
    to approve something already approved.
    """
    if event.event_type != IntegrationEventType.CONTENT_APPROVAL_REQUESTED.value:
        return True
    status = (
        await session.execute(
            select(ContentAsset.status).where(ContentAsset.id == event.subject_id)
        )
    ).scalar_one_or_none()
    return status is ContentAssetStatus.AWAITING_APPROVAL


async def fan_out(
    session: AsyncSession, now: datetime, limit: int = FAN_OUT_BATCH
) -> tuple[int, int]:
    """Returns (deliveries created, events processed)."""
    events = (
        (
            await session.execute(
                select(IntegrationEvent)
                .where(IntegrationEvent.fanned_out_at.is_(None))
                .order_by(IntegrationEvent.occurred_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    created = 0
    max_attempts = get_settings().webhook_max_attempts
    for event in events:
        if not await _still_relevant(session, event):
            event.fanned_out_at = now  # consumed without deliveries
            continue
        subs = (
            await session.execute(
                select(WebhookSubscription.id).where(
                    WebhookSubscription.revoked_at.is_(None),
                    WebhookSubscription.event_types.contains([event.event_type]),
                    WebhookSubscription.brand_ids.contains([event.brand_id]),
                    # No history: a new subscription only receives events from its creation on.
                    WebhookSubscription.created_at <= event.occurred_at,
                )
            )
        ).scalars()
        for sub_id in subs:
            result = await session.execute(
                pg_insert(WebhookDelivery)
                .values(
                    id=uuid7(),
                    event_id=event.id,
                    subscription_id=sub_id,
                    state=WebhookDeliveryStatus.PENDING.value,
                    attempt_count=0,
                    max_attempts=max_attempts,
                    next_attempt_at=now,
                )
                .on_conflict_do_nothing(index_elements=["event_id", "subscription_id"])
                .returning(WebhookDelivery.id)
            )
            created += len(result.scalars().all())
        event.fanned_out_at = now
    await session.commit()
    return created, len(events)


# ------------------------------------------------------------------ delivery
@dataclass(frozen=True, slots=True)
class Claimed:
    id: uuid.UUID
    attempt: int
    max_attempts: int


async def fail_exhausted_leases(session: AsyncSession, now: datetime) -> int:
    result = await session.execute(
        update(WebhookDelivery)
        .where(
            WebhookDelivery.state == WebhookDeliveryStatus.DELIVERING.value,
            WebhookDelivery.lease_until < now,
            WebhookDelivery.attempt_count >= WebhookDelivery.max_attempts,
        )
        .values(
            state=WebhookDeliveryStatus.FAILED.value,
            lease_until=None,
            last_error="lease expired on the last attempt (sender stopped)",
            updated_at=text("now()"),
        )
        .returning(WebhookDelivery.id)
    )
    count = len(result.scalars().all())
    await session.commit()
    return count


async def claim_due(session: AsyncSession, now: datetime, limit: int = BATCH) -> list[Claimed]:
    due = (
        select(WebhookDelivery.id)
        .where(
            WebhookDelivery.attempt_count < WebhookDelivery.max_attempts,
            or_(
                and_(
                    WebhookDelivery.state == WebhookDeliveryStatus.PENDING.value,
                    WebhookDelivery.next_attempt_at <= now,
                ),
                and_(
                    WebhookDelivery.state == WebhookDeliveryStatus.DELIVERING.value,
                    WebhookDelivery.lease_until < now,
                ),
            ),
        )
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    rows = await session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id.in_(due))
        .values(
            state=WebhookDeliveryStatus.DELIVERING.value,
            lease_until=now + LEASE,
            attempt_count=WebhookDelivery.attempt_count + 1,
            updated_at=text("now()"),
        )
        .returning(WebhookDelivery.id, WebhookDelivery.attempt_count, WebhookDelivery.max_attempts)
    )
    claimed = [Claimed(r.id, r.attempt_count, r.max_attempts) for r in rows]
    await session.commit()
    return claimed


async def _finish(
    session: AsyncSession,
    claimed: Claimed,
    subscription_id: uuid.UUID,
    *,
    status_code: int | None,
    error: str | None,
    now: datetime,
) -> str:
    success = status_code is not None and 200 <= status_code < 300
    gone = status_code == 410 or error == "subscription revoked"
    if success:
        state = WebhookDeliveryStatus.SUCCEEDED.value
    elif gone or claimed.attempt >= claimed.max_attempts:
        state = WebhookDeliveryStatus.FAILED.value
    else:
        state = WebhookDeliveryStatus.PENDING.value
    fenced = await session.execute(
        update(WebhookDelivery)
        .where(
            WebhookDelivery.id == claimed.id,
            WebhookDelivery.state == WebhookDeliveryStatus.DELIVERING.value,
            WebhookDelivery.attempt_count == claimed.attempt,
        )
        .values(
            state=state,
            lease_until=None,
            last_status_code=status_code,
            last_error=None if success else (error or f"HTTP {status_code}"),
            delivered_at=now if success else None,
            next_attempt_at=now + backoff(claimed.attempt) if state == "PENDING" else now,
            updated_at=text("now()"),
        )
        .returning(WebhookDelivery.id)
    )
    if not fenced.scalars().all():
        await session.rollback()
        return "SUPERSEDED"  # another sender owns a newer attempt of this delivery
    touched = update(WebhookSubscription).where(WebhookSubscription.id == subscription_id)
    if success:
        await session.execute(touched.values(last_success_at=now))
    else:
        await session.execute(touched.values(last_failure_at=now))
    await session.commit()
    return state


async def deliver(session: AsyncSession, claimed: Claimed, send: Sender = safe_post) -> str:
    row = await session.get(WebhookDelivery, claimed.id)
    if row is None:
        return "MISSING"
    event = await session.get(IntegrationEvent, row.event_id)
    sub = await session.get(WebhookSubscription, row.subscription_id)
    assert event is not None and sub is not None  # noqa: S101 - FKs
    now = datetime.now(UTC)
    sub_id, event_type = sub.id, event.event_type
    if sub.revoked_at is not None:
        return await _finish(
            session, claimed, sub_id, status_code=None, error="subscription revoked", now=now
        )
    body = payload(event)
    timestamp = int(time.time())
    delivery_id = str(row.id)
    secrets = active_secrets("subscription", sub, now)  # current + previous during grace
    headers = {
        sig.EVENT_HEADER: str(event.id),
        sig.EVENT_TYPE_HEADER: event_type,
        sig.DELIVERY_HEADER: delivery_id,
        sig.TIMESTAMP_HEADER: str(timestamp),
        sig.SIGNATURE_HEADER: sig.signature_header(secrets, timestamp, delivery_id, body),
        "X-OmniGrowth-Attempt": str(claimed.attempt),
    }
    target = sub.target_url
    # Plain values only from here: no transaction stays open during the network call, and
    # rolled-back ORM objects must not be touched again.
    await session.rollback()
    # A lease long enough for this one send (the batch is sent one by one).
    lease = timedelta(seconds=get_settings().webhook_timeout_s + 30)
    await session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == claimed.id, WebhookDelivery.attempt_count == claimed.attempt)
        .values(lease_until=datetime.now(UTC) + lease)
    )
    await session.commit()
    status_code: int | None = None
    error: str | None = None
    try:
        status_code = await send(
            target,
            body=body,
            headers=headers,
            allowlist=target_allowlist(),
            timeout_s=get_settings().webhook_timeout_s,
        )
    except OmniGrowthError as exc:
        # Only a reason code is kept: never the URL (n8n paths are secrets) or a body.
        error = f"{exc.code.value}: {exc.details.get('reason', exc.message)}"
    state = await _finish(
        session, claimed, sub_id, status_code=status_code, error=error, now=datetime.now(UTC)
    )
    log.info(
        "webhook delivery",
        extra={
            "delivery_id": str(claimed.id),
            "event_type": event_type,
            "attempt": claimed.attempt,
            "status_code": status_code,
            "result": state,
        },
    )
    return state


RETENTION = timedelta(days=30)


async def purge_old(session: AsyncSession, now: datetime) -> int:
    """Delete finished deliveries and fully handled events older than ``RETENTION``.

    Operational rows (like expired idempotency keys), not domain data; audit rows stay.
    """
    cutoff = now - RETENTION
    done = (WebhookDeliveryStatus.SUCCEEDED.value, WebhookDeliveryStatus.FAILED.value)
    deleted = await session.execute(
        delete(WebhookDelivery)
        .where(WebhookDelivery.state.in_(done), WebhookDelivery.updated_at < cutoff)
        .returning(WebhookDelivery.id)
    )
    count = len(deleted.scalars().all())
    open_delivery = select(WebhookDelivery.id).where(
        WebhookDelivery.event_id == IntegrationEvent.id
    )
    events = await session.execute(
        delete(IntegrationEvent)
        .where(IntegrationEvent.occurred_at < cutoff, ~open_delivery.exists())
        .returning(IntegrationEvent.id)
    )
    count += len(events.scalars().all())
    await session.commit()
    return count


async def dispatch_once(
    session_factory: Callable[[], Any], now: datetime | None = None, send: Sender = safe_post
) -> dict[str, int]:
    """One pass: fan out new events, then attempt every due delivery (bounded batch)."""
    counts = {"fanned_out": 0, "claimed": 0, "SUCCEEDED": 0, "PENDING": 0, "FAILED": 0}
    current = now or datetime.now(UTC)
    try:
        sig.server_key()
    except OmniGrowthError:
        return counts  # webhooks not configured: no database work at all
    async with session_factory() as session:
        counts["purged"] = await purge_old(session, current)
    async with session_factory() as session:
        counts["failed_leases"] = await fail_exhausted_leases(session, current)
        # Drain the outbox (bounded): a backlog of events must not starve new ones.
        for _ in range(FAN_OUT_PASSES):
            created, events = await fan_out(session, current)
            counts["fanned_out"] += created
            if events < FAN_OUT_BATCH:
                break
        claimed = await claim_due(session, current)
    counts["claimed"] = len(claimed)
    for item in claimed:
        async with session_factory() as session:
            state = await deliver(session, item, send)
        counts[state] = counts.get(state, 0) + 1
    return counts
