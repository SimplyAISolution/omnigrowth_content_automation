"""``/api/v1/webhooks`` — integrations (n8n), API_PLAN §2.18; Phase 12.

Management (endpoints, subscriptions, deliveries) is OWNER-only; secrets appear once, at
creation or rotation. ``POST /webhooks/inbound/{endpoint_key}`` authenticates with HMAC (no
bearer token), is rate limited per endpoint + client address, and can only run the endpoint's
allowed operations for its brands.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import JSONResponse

from omnigrowth.api.deps import JobQueueDep, OwnerDep, RateLimiterDep, SessionDep
from omnigrowth.api.routes.jobs import to_read
from omnigrowth.core.enums import WebhookDeliveryStatus
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.webhooks import (
    WebhookAcceptedRead,
    WebhookDeliveryRead,
    WebhookEndpointCreate,
    WebhookEndpointCreatedRead,
    WebhookEndpointRead,
    WebhookSubscriptionCreate,
    WebhookSubscriptionCreatedRead,
    WebhookSubscriptionRead,
)
from omnigrowth.security import webhooks as sig
from omnigrowth.security.ratelimit import parse_limits
from omnigrowth.services import integrations as service
from omnigrowth.services import jobs as job_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
INBOUND_LIMITS = parse_limits("60/minute")  # API_PLAN §6.4
CLIENT_LIMITS = parse_limits("120/minute")


# ------------------------------------------------------------------ inbound endpoints
@router.get("/endpoints", response_model=Page[WebhookEndpointRead])
async def list_endpoints(_principal: OwnerDep, session: SessionDep) -> Page[WebhookEndpointRead]:
    return Page[WebhookEndpointRead](items=await service.list_endpoints(session))


@router.post(
    "/endpoints", response_model=WebhookEndpointCreatedRead, status_code=status.HTTP_201_CREATED
)
async def create_endpoint(
    body: WebhookEndpointCreate, principal: OwnerDep, session: SessionDep
) -> WebhookEndpointCreatedRead:
    return await service.create_endpoint(session, principal.actor, body)


@router.post("/endpoints/{endpoint_id}/rotate-secret", response_model=WebhookEndpointCreatedRead)
async def rotate_endpoint_secret(
    endpoint_id: uuid.UUID, principal: OwnerDep, session: SessionDep
) -> WebhookEndpointCreatedRead:
    return await service.rotate_endpoint(session, principal.actor, endpoint_id)


@router.delete("/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_endpoint(
    endpoint_id: uuid.UUID, principal: OwnerDep, session: SessionDep
) -> Response:
    """Revokes at once (every secret stops working); the row stays for the audit trail."""
    await service.revoke_endpoint(session, principal.actor, endpoint_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ outbound subscriptions
@router.get("/subscriptions", response_model=Page[WebhookSubscriptionRead])
async def list_subscriptions(
    _principal: OwnerDep, session: SessionDep
) -> Page[WebhookSubscriptionRead]:
    return Page[WebhookSubscriptionRead](items=await service.list_subscriptions(session))


@router.post(
    "/subscriptions",
    response_model=WebhookSubscriptionCreatedRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription(
    body: WebhookSubscriptionCreate, principal: OwnerDep, session: SessionDep
) -> WebhookSubscriptionCreatedRead:
    return await service.create_subscription(session, principal.actor, body)


@router.post(
    "/subscriptions/{subscription_id}/rotate-secret", response_model=WebhookSubscriptionCreatedRead
)
async def rotate_subscription_secret(
    subscription_id: uuid.UUID, principal: OwnerDep, session: SessionDep
) -> WebhookSubscriptionCreatedRead:
    return await service.rotate_subscription(session, principal.actor, subscription_id)


@router.post(
    "/subscriptions/{subscription_id}/test",
    response_model=WebhookDeliveryRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_test_event(
    subscription_id: uuid.UUID, principal: OwnerDep, session: SessionDep
) -> WebhookDeliveryRead:
    """Queue an ``integration.ping`` to this subscription only (delivered by the scheduler)."""
    return await service.send_test_event(session, principal.actor, subscription_id)


@router.delete("/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_subscription(
    subscription_id: uuid.UUID, principal: OwnerDep, session: SessionDep
) -> Response:
    await service.revoke_subscription(session, principal.actor, subscription_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ deliveries
@router.get("/deliveries", response_model=Page[WebhookDeliveryRead])
async def list_deliveries(
    _principal: OwnerDep,
    session: SessionDep,
    status_filter: Annotated[WebhookDeliveryStatus | None, Query(alias="status")] = None,
    subscription_id: uuid.UUID | None = None,
) -> Page[WebhookDeliveryRead]:
    items = await service.list_deliveries(
        session, status=status_filter, subscription_id=subscription_id
    )
    return Page[WebhookDeliveryRead](items=items)


@router.post("/deliveries/{delivery_id}/redeliver", response_model=WebhookDeliveryRead)
async def redeliver(
    delivery_id: uuid.UUID, principal: OwnerDep, session: SessionDep
) -> WebhookDeliveryRead:
    return await service.redeliver(session, principal.actor, delivery_id)


# ------------------------------------------------------------------ inbound (HMAC)
@router.post(
    "/inbound/{endpoint_key}",
    response_model=WebhookAcceptedRead,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"model": WebhookAcceptedRead, "description": "Replay of an earlier delivery"}},
)
async def inbound(
    endpoint_key: str,
    request: Request,
    session: SessionDep,
    queue: JobQueueDep,
    limiter: RateLimiterDep,
) -> JSONResponse:
    client = request.client.host if request.client else "unknown"
    # Per client first (varying the key must not buy fresh buckets), then per endpoint.
    await limiter.hit("webhook-inbound-client", client, CLIENT_LIMITS, fail_closed=True)
    await limiter.hit(
        "webhook-inbound", f"{endpoint_key}:{client}", INBOUND_LIMITS, fail_closed=True
    )
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    endpoint = await service.authenticate(session, endpoint_key, body, headers)

    async def job_read(job_id: uuid.UUID) -> Any:
        return to_read(await job_service.get_job(session, job_id)).model_dump(mode="json")

    result = await service.handle_inbound(
        session, queue, endpoint, headers.get(sig.DELIVERY_HEADER.lower()), body, job_read
    )
    return JSONResponse(result.body, status_code=200 if result.replayed else result.status)
