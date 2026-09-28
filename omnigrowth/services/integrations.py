"""Integrations (n8n), Phase 12: inbound endpoints, outbound subscriptions, deliveries.

The INTEGRATION principal is an inbound endpoint: HMAC-authenticated, limited to its
``brand_ids`` and ``allowed_operations`` (source text/URL ingest, campaign planning). It acts as
``SYSTEM`` actor ``integration:<key>`` through the same services and safeguards as people, and
it can never approve, schedule, publish, reconcile, or touch accounts, credentials or settings:
those operations do not exist for it. Managing integrations is OWNER-only and audited.
OmniGrowth stays the authority for permissions, approval and publication state; an event
reaching n8n changes nothing here.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import (
    ActorType,
    IntegrationEventType,
    IntegrationOperation,
    WebhookDeliveryStatus,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.settings import get_settings
from omnigrowth.core.state_machines import Actor
from omnigrowth.models import Brand, Campaign
from omnigrowth.models.integrations import (
    IntegrationEvent,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookSubscription,
)
from omnigrowth.observability.audit import record_audit
from omnigrowth.schemas.webhooks import (
    CampaignGeneratePayload,
    TextIngestPayload,
    UrlIngestPayload,
    WebhookDeliveryRead,
    WebhookEndpointCreate,
    WebhookEndpointCreatedRead,
    WebhookEndpointRead,
    WebhookInboundEnvelope,
    WebhookSubscriptionCreate,
    WebhookSubscriptionCreatedRead,
    WebhookSubscriptionRead,
)
from omnigrowth.security import webhooks as sig
from omnigrowth.security.http import check_webhook_target, parse_host_allowlist
from omnigrowth.services import campaigns as campaign_service
from omnigrowth.services import idempotency as idem_store
from omnigrowth.services import sources as source_service
from omnigrowth.services.jobs import JobQueue

INBOUND_PREFIX = "/api/v1/webhooks/inbound/"
MAX_INBOUND_BYTES = 1024 * 1024  # API_PLAN §6.3


def _not_found(what: str) -> OmniGrowthError:
    return OmniGrowthError(ErrorCode.NOT_FOUND, f"{what} not found")


def target_allowlist() -> frozenset[str]:
    """``OMNIGROWTH_WEBHOOK_TARGET_ALLOWLIST`` as normalised ``host:port`` entries."""
    return parse_host_allowlist(
        get_settings().webhook_target_allowlist, "OMNIGROWTH_WEBHOOK_TARGET_ALLOWLIST"
    )


def active_secrets(
    kind: sig.Kind, row: WebhookEndpoint | WebhookSubscription, now: datetime
) -> list[str]:
    """Current secret, plus the previous one while its grace period lasts. None if revoked."""
    if row.revoked_at is not None:
        return []
    secrets = [sig.derive_secret(kind, row.id, row.secret_version)]
    if (
        row.previous_secret_version is not None
        and row.previous_secret_valid_until is not None
        and row.previous_secret_valid_until > now
    ):
        secrets.append(sig.derive_secret(kind, row.id, row.previous_secret_version))
    return secrets


async def _require_brands(session: AsyncSession, brand_ids: list[uuid.UUID]) -> None:
    found = set(
        (
            await session.execute(
                select(Brand.id).where(Brand.id.in_(brand_ids), Brand.archived_at.is_(None))
            )
        ).scalars()
    )
    missing = sorted(str(b) for b in set(brand_ids) - found)
    if missing:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "unknown or archived brand",
            details={"brand_ids": missing},
        )


# ------------------------------------------------------------------ read models
def endpoint_read(row: WebhookEndpoint) -> WebhookEndpointRead:
    return WebhookEndpointRead(
        id=row.id,
        key=row.key,
        description=row.description,
        brand_ids=list(row.brand_ids),
        allowed_operations=[IntegrationOperation(o) for o in row.allowed_operations],
        inbound_path=f"{INBOUND_PREFIX}{row.key}",
        secret_version=row.secret_version,
        secret_fingerprint=sig.fingerprint(
            sig.derive_secret("endpoint", row.id, row.secret_version)
        ),
        secret_rotated_at=row.secret_rotated_at,
        previous_secret_valid_until=row.previous_secret_valid_until,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def subscription_read(row: WebhookSubscription) -> WebhookSubscriptionRead:
    return WebhookSubscriptionRead(
        id=row.id,
        target_url=row.target_url,
        description=row.description,
        brand_ids=list(row.brand_ids),
        event_types=[IntegrationEventType(e) for e in row.event_types],
        secret_version=row.secret_version,
        secret_fingerprint=sig.fingerprint(
            sig.derive_secret("subscription", row.id, row.secret_version)
        ),
        secret_rotated_at=row.secret_rotated_at,
        previous_secret_valid_until=row.previous_secret_valid_until,
        revoked_at=row.revoked_at,
        last_success_at=row.last_success_at,
        last_failure_at=row.last_failure_at,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _audit_view(row: WebhookEndpoint | WebhookSubscription) -> dict[str, Any]:
    """What an audit row records: scope and version, never a secret."""
    common: dict[str, Any] = {
        "brand_ids": [str(b) for b in row.brand_ids],
        "secret_version": row.secret_version,
        "revoked": row.revoked_at is not None,
    }
    if isinstance(row, WebhookEndpoint):
        return {**common, "key": row.key, "allowed_operations": list(row.allowed_operations)}
    return {**common, "event_types": list(row.event_types)}


async def _audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    row: WebhookEndpoint | WebhookSubscription,
    **extra: Any,
) -> None:
    await record_audit(
        session,
        actor=actor,
        action=action,
        entity_type=row.__tablename__,
        entity_id=row.id,
        brand_id=row.brand_ids[0] if row.brand_ids else None,
        after=_audit_view(row),
        metadata=extra or None,
    )


# ------------------------------------------------------------------ endpoints (inbound)
async def create_endpoint(
    session: AsyncSession, actor: Actor, body: WebhookEndpointCreate
) -> WebhookEndpointCreatedRead:
    sig.server_key()  # refuse early when webhooks are not configured
    await _require_brands(session, body.brand_ids)
    exists = await session.execute(
        select(WebhookEndpoint.id).where(WebhookEndpoint.key == body.key)
    )
    if exists.scalar_one_or_none() is not None:
        raise OmniGrowthError(ErrorCode.CONFLICT, "an endpoint with this key already exists")
    row = WebhookEndpoint(
        id=uuid7(),
        key=body.key,
        description=body.description,
        brand_ids=list(dict.fromkeys(body.brand_ids)),
        allowed_operations=sorted({o.value for o in body.allowed_operations}),
        created_by=f"{actor.type.value}:{actor.id}",
    )
    session.add(row)
    await session.flush([row])
    await _audit(session, actor, "webhook_endpoint.created", row)
    await session.commit()
    await session.refresh(row)
    secret = sig.derive_secret("endpoint", row.id, row.secret_version)
    return WebhookEndpointCreatedRead(**endpoint_read(row).model_dump(), secret=secret)


async def list_endpoints(session: AsyncSession) -> list[WebhookEndpointRead]:
    rows = await session.execute(select(WebhookEndpoint).order_by(WebhookEndpoint.created_at))
    return [endpoint_read(r) for r in rows.scalars()]


async def _endpoint(session: AsyncSession, endpoint_id: uuid.UUID) -> WebhookEndpoint:
    row = await session.get(WebhookEndpoint, endpoint_id, with_for_update=True)
    if row is None:
        raise _not_found("webhook endpoint")
    return row


def _rotate(row: WebhookEndpoint | WebhookSubscription, now: datetime) -> None:
    if row.revoked_at is not None:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION, "a revoked credential cannot be rotated"
        )
    if row.previous_secret_valid_until is not None and row.previous_secret_valid_until > now:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "a rotation is still in its grace period; wait for it to end, or revoke and create "
            "a new credential if the secret is compromised",
            details={"previous_secret_valid_until": row.previous_secret_valid_until.isoformat()},
        )
    row.previous_secret_version = row.secret_version
    row.previous_secret_valid_until = now + timedelta(
        hours=get_settings().webhook_secret_grace_hours
    )
    row.secret_version += 1
    row.secret_rotated_at = now


async def rotate_endpoint(
    session: AsyncSession, actor: Actor, endpoint_id: uuid.UUID
) -> WebhookEndpointCreatedRead:
    sig.server_key()
    row = await _endpoint(session, endpoint_id)
    _rotate(row, datetime.now(UTC))
    await _audit(session, actor, "webhook_endpoint.secret_rotated", row)
    await session.commit()
    await session.refresh(row)
    secret = sig.derive_secret("endpoint", row.id, row.secret_version)
    return WebhookEndpointCreatedRead(**endpoint_read(row).model_dump(), secret=secret)


async def revoke_endpoint(session: AsyncSession, actor: Actor, endpoint_id: uuid.UUID) -> None:
    row = await _endpoint(session, endpoint_id)
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        row.revoked_by = f"{actor.type.value}:{actor.id}"
        await _audit(session, actor, "webhook_endpoint.revoked", row)
    await session.commit()


# ------------------------------------------------------------------ subscriptions (outbound)
async def create_subscription(
    session: AsyncSession, actor: Actor, body: WebhookSubscriptionCreate
) -> WebhookSubscriptionCreatedRead:
    sig.server_key()
    url = str(body.target_url)
    check_webhook_target(url, target_allowlist())  # 422 SSRF_BLOCKED on a disallowed target
    await _require_brands(session, body.brand_ids)
    row = WebhookSubscription(
        id=uuid7(),
        target_url=url,
        description=body.description,
        brand_ids=list(dict.fromkeys(body.brand_ids)),
        event_types=sorted({e.value for e in body.event_types}),
        created_by=f"{actor.type.value}:{actor.id}",
    )
    session.add(row)
    await session.flush([row])
    await _audit(session, actor, "webhook_subscription.created", row)
    await session.commit()
    await session.refresh(row)
    secret = sig.derive_secret("subscription", row.id, row.secret_version)
    return WebhookSubscriptionCreatedRead(**subscription_read(row).model_dump(), secret=secret)


async def list_subscriptions(session: AsyncSession) -> list[WebhookSubscriptionRead]:
    rows = await session.execute(
        select(WebhookSubscription).order_by(WebhookSubscription.created_at)
    )
    return [subscription_read(r) for r in rows.scalars()]


async def _subscription(session: AsyncSession, subscription_id: uuid.UUID) -> WebhookSubscription:
    row = await session.get(WebhookSubscription, subscription_id, with_for_update=True)
    if row is None:
        raise _not_found("webhook subscription")
    return row


async def rotate_subscription(
    session: AsyncSession, actor: Actor, subscription_id: uuid.UUID
) -> WebhookSubscriptionCreatedRead:
    sig.server_key()
    row = await _subscription(session, subscription_id)
    _rotate(row, datetime.now(UTC))
    await _audit(session, actor, "webhook_subscription.secret_rotated", row)
    await session.commit()
    await session.refresh(row)
    secret = sig.derive_secret("subscription", row.id, row.secret_version)
    return WebhookSubscriptionCreatedRead(**subscription_read(row).model_dump(), secret=secret)


async def revoke_subscription(
    session: AsyncSession, actor: Actor, subscription_id: uuid.UUID
) -> None:
    """Stops future deliveries; pending ones fail as 'subscription revoked' (kept for audit)."""
    row = await _subscription(session, subscription_id)
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        row.revoked_by = f"{actor.type.value}:{actor.id}"
        await _audit(session, actor, "webhook_subscription.revoked", row)
    await session.commit()


async def send_test_event(
    session: AsyncSession, actor: Actor, subscription_id: uuid.UUID
) -> WebhookDeliveryRead:
    """Queue an ``integration.ping`` to this subscription only (setup check)."""
    row = await _subscription(session, subscription_id)
    if row.revoked_at is not None:
        raise OmniGrowthError(ErrorCode.INVALID_STATE_TRANSITION, "the subscription is revoked")
    now = datetime.now(UTC)
    event = IntegrationEvent(
        id=uuid.uuid4(),
        event_type=IntegrationEventType.INTEGRATION_PING.value,
        brand_id=row.brand_ids[0],
        subject_type="webhook_subscriptions",
        subject_id=row.id,
        data={"subscription_id": str(row.id), "message": "OmniGrowth test event"},
        occurred_at=now,
        fanned_out_at=now,  # addressed to this subscription only
    )
    session.add(event)
    await session.flush([event])
    delivery = WebhookDelivery(
        id=uuid7(),
        event_id=event.id,
        subscription_id=row.id,
        state=WebhookDeliveryStatus.PENDING.value,
        attempt_count=0,
        max_attempts=get_settings().webhook_max_attempts,
        next_attempt_at=now,
    )
    session.add(delivery)
    await session.flush([delivery])
    await _audit(session, actor, "webhook_subscription.test_sent", row, event_id=str(event.id))
    await session.commit()
    return delivery_read(delivery, event)


# ------------------------------------------------------------------ deliveries (inspection)
def delivery_read(d: WebhookDelivery, e: IntegrationEvent) -> WebhookDeliveryRead:
    return WebhookDeliveryRead(
        id=d.id,
        event_id=d.event_id,
        event_type=IntegrationEventType(e.event_type),
        brand_id=e.brand_id,
        subscription_id=d.subscription_id,
        status=WebhookDeliveryStatus(d.state),
        attempt_count=d.attempt_count,
        max_attempts=d.max_attempts,
        next_attempt_at=d.next_attempt_at,
        last_status_code=d.last_status_code,
        last_error=d.last_error,
        delivered_at=d.delivered_at,
        occurred_at=e.occurred_at,
        created_at=d.created_at,
    )


async def list_deliveries(
    session: AsyncSession,
    *,
    status: WebhookDeliveryStatus | None,
    subscription_id: uuid.UUID | None,
    limit: int = 200,
) -> list[WebhookDeliveryRead]:
    stmt = select(WebhookDelivery, IntegrationEvent).join(
        IntegrationEvent, IntegrationEvent.id == WebhookDelivery.event_id
    )
    if status is not None:
        stmt = stmt.where(WebhookDelivery.state == status.value)
    if subscription_id is not None:
        stmt = stmt.where(WebhookDelivery.subscription_id == subscription_id)
    rows = await session.execute(stmt.order_by(WebhookDelivery.created_at.desc()).limit(limit))
    return [delivery_read(d, e) for d, e in rows.all()]


async def redeliver(
    session: AsyncSession, actor: Actor, delivery_id: uuid.UUID
) -> WebhookDeliveryRead:
    """A FAILED delivery gets a fresh attempt budget (same event id: receivers dedupe)."""
    d = await session.get(WebhookDelivery, delivery_id, with_for_update=True)
    if d is None:
        raise _not_found("webhook delivery")
    if d.state != WebhookDeliveryStatus.FAILED.value:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION,
            "only failed deliveries can be redelivered",
            details={"state": d.state},
        )
    sub = await session.get(WebhookSubscription, d.subscription_id)
    if sub is None or sub.revoked_at is not None:
        raise OmniGrowthError(ErrorCode.INVALID_STATE_TRANSITION, "the subscription is revoked")
    d.state = WebhookDeliveryStatus.PENDING.value
    d.attempt_count = 0
    d.next_attempt_at = datetime.now(UTC)
    d.lease_until = None
    await _audit(session, actor, "webhook_delivery.redelivered", sub, delivery_id=str(d.id))
    await session.commit()
    event = await session.get(IntegrationEvent, d.event_id)
    assert event is not None  # noqa: S101 - FK
    return delivery_read(d, event)


# ------------------------------------------------------------ inbound (the INTEGRATION principal)
@dataclass(frozen=True, slots=True)
class InboundResult:
    status: int
    body: dict[str, Any]
    replayed: bool


def _unauthorized() -> OmniGrowthError:
    # One generic answer for unknown key, revoked endpoint, stale timestamp and bad signature.
    return OmniGrowthError(ErrorCode.UNAUTHORIZED, "webhook authentication failed")


async def authenticate(
    session: AsyncSession, key: str, body: bytes, headers: dict[str, str]
) -> WebhookEndpoint:
    if len(body) > MAX_INBOUND_BYTES:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "payload too large",
            details={"reason": "TOO_LARGE", "limit": "1 MiB"},
        )
    sig.server_key()
    row = (
        await session.execute(select(WebhookEndpoint).where(WebhookEndpoint.key == key))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    secrets = active_secrets("endpoint", row, now) if row is not None else []
    ok = sig.verify(
        secrets or ["whsec_invalid"],  # same work when the endpoint does not exist
        headers.get(sig.TIMESTAMP_HEADER.lower()),
        headers.get(sig.SIGNATURE_HEADER.lower()),
        headers.get(sig.DELIVERY_HEADER.lower()),  # signed: bound to this body
        body,
        time.time(),
    )
    if row is None or not secrets or not ok:
        raise _unauthorized()
    return row


def _in_scope(endpoint: WebhookEndpoint, brand_id: uuid.UUID) -> None:
    if brand_id not in set(endpoint.brand_ids):
        raise OmniGrowthError(
            ErrorCode.FORBIDDEN,
            "this integration is not allowed to act for that brand",
            details={"reason": "BRAND_OUT_OF_SCOPE"},
        )


async def handle_inbound(
    session: AsyncSession,
    queue: JobQueue,
    endpoint: WebhookEndpoint,
    delivery_id: str | None,
    body: bytes,
    run_job_read: Any,
) -> InboundResult:
    """Run one authenticated inbound operation exactly once per delivery id."""
    if not delivery_id:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            f"{sig.DELIVERY_HEADER} header is required",
            details={"reason": "DELIVERY_ID_REQUIRED"},
        )
    try:
        envelope = WebhookInboundEnvelope.model_validate_json(body)
    except ValidationError as exc:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "invalid webhook body",
            details={"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
        ) from exc
    operation = envelope.operation
    if operation.value not in endpoint.allowed_operations:
        raise OmniGrowthError(
            ErrorCode.FORBIDDEN,
            "this integration is not allowed to perform that operation",
            details={"reason": "OPERATION_NOT_ALLOWED", "operation": operation.value},
        )
    claim = await idem_store.begin(
        principal_id=endpoint.id,
        principal_type="WEBHOOK_ENDPOINT",
        method="POST",
        route=f"{INBOUND_PREFIX}{{endpoint_key}}",
        key=delivery_id,
        body_hash=hashlib.sha256(body).hexdigest(),
    )
    if isinstance(claim, idem_store.Replay):
        stored = dict(claim.body or {})
        stored["replayed"] = True
        return InboundResult(status=claim.status, body=stored, replayed=True)
    # Its own actor type: the state machines allow it only SA1, C2, C5 and JB1.
    actor = Actor(ActorType.INTEGRATION, f"integration:{endpoint.key}")
    completed = False
    try:
        result = await _run_operation(
            session, queue, endpoint, operation, envelope.payload, actor, run_job_read
        )
        body_out: dict[str, Any] = {
            "delivery_id": delivery_id,
            "operation": operation.value,
            "replayed": False,
            **result,
        }
        live = await session.get(WebhookEndpoint, endpoint.id)
        if live is not None:
            live.last_used_at = datetime.now(UTC)
        await record_audit(
            session,
            actor=actor,
            action="integration.inbound",
            entity_type="webhook_endpoints",
            entity_id=endpoint.id,
            brand_id=endpoint.brand_ids[0],
            metadata={
                "operation": operation.value,
                "delivery_id": delivery_id,
                "result_ref": result.get("result_ref"),
            },
        )
        await session.commit()
        await idem_store.complete(claim, 202, body_out)
        completed = True
        return InboundResult(status=202, body=body_out, replayed=False)
    finally:
        if not completed:
            await idem_store.abandon(claim)


def _payload(model: Any, payload: dict[str, Any]) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "invalid payload for this operation",
            details={"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
        ) from exc


async def _run_operation(
    session: AsyncSession,
    queue: JobQueue,
    endpoint: WebhookEndpoint,
    operation: IntegrationOperation,
    payload: dict[str, Any],
    actor: Actor,
    run_job_read: Any,
) -> dict[str, Any]:
    if operation is IntegrationOperation.SOURCE_TEXT_INGEST:
        text = _payload(TextIngestPayload, payload)
        _in_scope(endpoint, text.brand_id)
        accepted = await source_service.ingest_text(
            session,
            actor=actor,
            brand_id=text.brand_id,
            source_type=text.source_type,
            content=text.content,
            title=text.title,
        )
        return {
            "job": None,
            "result_ref": {"source_id": str(accepted.source.id), "duplicate": accepted.duplicate},
        }
    if operation is IntegrationOperation.SOURCE_URL_INGEST:
        url = _payload(UrlIngestPayload, payload)
        _in_scope(endpoint, url.brand_id)
        accepted = await source_service.accept_url(
            session,
            actor=actor,
            queue=queue,
            brand_id=url.brand_id,
            url=url.url,
            source_type=url.source_type,
            title=url.title,
        )
        job = await run_job_read(accepted.job_id) if accepted.job_id else None
        return {"job": job, "result_ref": {"source_id": str(accepted.source.id)}}
    generate = _payload(CampaignGeneratePayload, payload)
    campaign = await session.get(Campaign, generate.campaign_id)
    if campaign is None or campaign.brand_id not in set(endpoint.brand_ids):
        raise _not_found("campaign")  # out-of-scope campaigns are indistinguishable from missing
    job_id = await campaign_service.start_planning(session, queue, actor, campaign.id)
    return {"job": await run_job_read(job_id), "result_ref": {"campaign_id": str(campaign.id)}}
