"""Phase 12 gate: n8n integration against a local receiver (no real n8n, email or chat).

Covers authorization and brand isolation, credential rotation and revocation, invalid
authentication and replay, duplicate delivery, retry exhaustion, receiver downtime and
recovery, simulated-publication labelling, and that nothing in the approval or publication
flow waits for (or is changed by) webhook delivery.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from omnigrowth.core.enums import UserRole
from omnigrowth.core.settings import get_settings
from omnigrowth.models import AuditEvent
from omnigrowth.models.integrations import IntegrationEvent, WebhookDelivery
from omnigrowth.services import webhook_delivery as wd
from omnigrowth.storage.db import get_sessionmaker
from tests.integration.campaign_support import (
    TZ,
    Env,
    approve_all,
    generated_campaign,
    platform_accounts,
)
from tests.integration.helpers import SKIP_IK, auth_headers
from tests.integration.webhook_receiver import Receiver

pytestmark = pytest.mark.integration


class Hooks:
    env: Env
    owner: dict[str, str]
    receiver: Receiver


@pytest.fixture
async def hooks(campaign_env: Env, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Hooks]:
    receiver = Receiver().start()
    monkeypatch.setenv("OMNIGROWTH_WEBHOOK_KEY", secrets.token_urlsafe(40))
    monkeypatch.setenv("OMNIGROWTH_WEBHOOK_TARGET_ALLOWLIST", f"127.0.0.1:{receiver.port}")
    monkeypatch.setenv("OMNIGROWTH_WEBHOOK_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("OMNIGROWTH_WEBHOOK_TIMEOUT_S", "3")
    monkeypatch.setenv("OMNIGROWTH_APP_URL", "http://127.0.0.1:5173")
    get_settings.cache_clear()
    h = Hooks()
    h.env, h.receiver = campaign_env, receiver
    h.owner = await auth_headers(campaign_env.client, UserRole.OWNER)
    try:
        yield h
    finally:
        receiver.stop()


async def _call(
    h: Hooks, method: str, path: str, headers: dict[str, str] | None = None, **kw: Any
) -> Any:
    return await h.env.client.request(method, f"/api/v1{path}", headers=headers or h.owner, **kw)


async def _endpoint(
    h: Hooks, operations: list[str], brand_ids: list[str] | None = None, key: str | None = None
) -> dict[str, Any]:
    resp = await _call(
        h,
        "POST",
        "/webhooks/endpoints",
        json={
            "key": key or f"n8n-{secrets.token_hex(3)}",
            "brand_ids": brand_ids or [h.env.brand_id],
            "allowed_operations": operations,
        },
    )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def _signed(
    secret: str,
    body: bytes,
    *,
    delivery: str | None = None,
    ts: int | None = None,
    signed_delivery: str | None = None,
) -> dict[str, str]:
    """Sign "<ts>.<delivery id>.<body>" (``signed_delivery`` forges a swapped id)."""
    stamp = ts if ts is not None else int(time.time())
    bound = signed_delivery if signed_delivery is not None else (delivery or "")
    mac = hmac.new(secret.encode(), f"{stamp}.{bound}.".encode() + body, hashlib.sha256).hexdigest()
    headers = {
        "X-OmniGrowth-Timestamp": str(stamp),
        "X-OmniGrowth-Signature": f"v1={mac}",
        "Content-Type": "application/json",
        SKIP_IK: "1",  # inbound webhooks use their delivery id, not an Idempotency-Key
    }
    if delivery is not None:
        headers["X-OmniGrowth-Delivery-ID"] = delivery
    return headers


async def _inbound(h: Hooks, key: str, secret: str, envelope: dict[str, Any], **kw: Any) -> Any:
    body = json.dumps(envelope).encode()
    delivery = kw.pop("delivery", f"dlv-{secrets.token_hex(8)}")
    return await h.env.client.post(
        f"/api/v1/webhooks/inbound/{key}",
        content=body,
        headers=_signed(secret, body, delivery=delivery, **kw),
    )


def _wizard_draft(env: Env) -> dict[str, Any]:
    from tests.integration.campaign_support import _wizard_body

    return dict(_wizard_body(env, generate=False))


def _text(
    brand_id: str, content: str = "Local AI keeps client data on the machine. " * 3
) -> dict[str, Any]:
    return {
        "operation": "source.text_ingest",
        "payload": {"brand_id": brand_id, "content": content, "title": "From n8n"},
    }


async def _other_brand(h: Hooks) -> str:
    resp = await _call(
        h,
        "POST",
        "/brands",
        json={
            "slug": f"other-{secrets.token_hex(3)}",
            "name": "Other",
            "display_name": "Other brand",
            "timezone": TZ,
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# ------------------------------------------------------------------ management
async def test_integration_management_is_owner_only_audited_and_shows_secrets_once(
    hooks: Hooks,
) -> None:
    for role in (UserRole.EDITOR, UserRole.VIEWER):
        headers = await auth_headers(hooks.env.client, role)
        assert (await _call(hooks, "GET", "/webhooks/endpoints", headers)).status_code == 403
        denied = await _call(
            hooks,
            "POST",
            "/webhooks/subscriptions",
            headers,
            json={
                "target_url": hooks.receiver.url,
                "brand_ids": [hooks.env.brand_id],
                "event_types": ["publication.failed"],
            },
        )
        assert denied.status_code == 403
    created = await _endpoint(hooks, ["source.text_ingest"])
    secret = created["secret"]
    assert secret.startswith("whsec_")
    listed = (await _call(hooks, "GET", "/webhooks/endpoints")).json()["items"]
    assert "secret" not in json.dumps(listed) or secret not in json.dumps(listed)
    assert secret not in json.dumps(listed)
    assert listed[0]["secret_fingerprint"] == hashlib.sha256(secret.encode()).hexdigest()[:8]
    async with get_sessionmaker()() as session:
        rows = (
            (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.entity_type == "webhook_endpoints")
                )
            )
            .scalars()
            .all()
        )
    assert [r.action for r in rows] == ["webhook_endpoint.created"]
    assert secret not in json.dumps(
        [r.after for r in rows] + [r.metadata_ for r in rows], default=str
    )
    # Unknown operations (e.g. approving) cannot even be granted.
    bad = await _call(
        hooks,
        "POST",
        "/webhooks/endpoints",
        json={
            "key": "cannot-approve",
            "brand_ids": [hooks.env.brand_id],
            "allowed_operations": ["content.approve"],
        },
    )
    assert bad.status_code == 422
    # A target that is neither public HTTPS nor allowlisted is refused.
    ssrf = await _call(
        hooks,
        "POST",
        "/webhooks/subscriptions",
        json={
            "target_url": "http://127.0.0.1:6379/",
            "brand_ids": [hooks.env.brand_id],
            "event_types": ["publication.failed"],
        },
    )
    assert ssrf.status_code == 422 and ssrf.json()["code"] == "SSRF_BLOCKED"


# ------------------------------------------------------------------ inbound
async def test_inbound_authentication_scope_replay_rotation_and_revocation(hooks: Hooks) -> None:
    other = await _other_brand(hooks)
    ep = await _endpoint(hooks, ["source.text_ingest"])
    key, secret = ep["key"], ep["secret"]

    ok = await _inbound(hooks, key, secret, _text(hooks.env.brand_id), delivery="dlv-first-0001")
    assert ok.status_code == 202, ok.text
    source_id = ok.json()["result_ref"]["source_id"]
    replay = await _inbound(
        hooks, key, secret, _text(hooks.env.brand_id), delivery="dlv-first-0001"
    )
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["result_ref"]["source_id"] == source_id  # executed once
    reused = await _inbound(
        hooks, key, secret, _text(hooks.env.brand_id, "different " * 10), delivery="dlv-first-0001"
    )
    assert reused.status_code == 409

    async with get_sessionmaker()() as session:
        audit = (
            (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.action == "integration.inbound")
                )
            )
            .scalars()
            .all()
        )
    assert [(a.actor_type.value, a.actor_id) for a in audit] == [
        ("INTEGRATION", f"integration:{key}")
    ]

    # Authentication failures all look the same (401), and nothing runs.
    body = json.dumps(_text(hooks.env.brand_id)).encode()
    stale = int(time.time()) - 400
    for headers in (
        _signed("whsec_wrong", body, delivery="dlv-bad-sig-01"),
        _signed(secret, body, delivery="dlv-stale-0001", ts=stale),
        {
            "Content-Type": "application/json",
            SKIP_IK: "1",
            "X-OmniGrowth-Delivery-ID": "dlv-nohdr-001",
        },
        {
            **hooks.owner,
            "Content-Type": "application/json",
            SKIP_IK: "1",
        },  # a bearer token is not HMAC
    ):
        resp = await hooks.env.client.post(
            f"/api/v1/webhooks/inbound/{key}", content=body, headers=headers
        )
        assert resp.status_code == 401, resp.text
    tampered = _signed(secret, body, delivery="dlv-tamper-01")
    resp = await hooks.env.client.post(
        f"/api/v1/webhooks/inbound/{key}", content=body + b" ", headers=tampered
    )
    assert resp.status_code == 401
    # The delivery id is signed: a captured request cannot be replayed under a fresh id.
    swapped = _signed(secret, body, delivery="dlv-swapped-02", signed_delivery="dlv-tamper-01")
    resp = await hooks.env.client.post(
        f"/api/v1/webhooks/inbound/{key}", content=body, headers=swapped
    )
    assert resp.status_code == 401
    unknown = await _inbound(hooks, "no-such-endpoint", secret, _text(hooks.env.brand_id))
    assert unknown.status_code == 401

    # Scope: other brands and other operations are refused.
    out = await _inbound(hooks, key, secret, _text(other))
    assert out.status_code == 403 and out.json()["details"]["reason"] == "BRAND_OUT_OF_SCOPE"
    op = await _inbound(
        hooks,
        key,
        secret,
        {"operation": "campaign.generate", "payload": {"campaign_id": str(uuid.uuid4())}},
    )
    assert op.status_code == 403 and op.json()["details"]["reason"] == "OPERATION_NOT_ALLOWED"
    approve = await _inbound(hooks, key, secret, {"operation": "content.approve", "payload": {}})
    assert approve.status_code == 422
    body = json.dumps(_text(hooks.env.brand_id)).encode()
    no_id = await hooks.env.client.post(
        f"/api/v1/webhooks/inbound/{key}", content=body, headers=_signed(secret, body)
    )
    assert no_id.status_code == 401  # the delivery id is part of what is signed

    # Rotation: the previous secret keeps working during the grace period.
    rotated = await _call(hooks, "POST", f"/webhooks/endpoints/{ep['id']}/rotate-secret")
    assert rotated.status_code == 200
    new_secret = rotated.json()["secret"]
    assert new_secret != secret and rotated.json()["secret_version"] == 2
    # A second rotation during the grace period would silently cut off the oldest secret.
    again = await _call(hooks, "POST", f"/webhooks/endpoints/{ep['id']}/rotate-secret")
    assert again.status_code == 409, again.text
    assert (
        await _inbound(hooks, key, new_secret, _text(hooks.env.brand_id, "new secret " * 10))
    ).status_code == 202
    assert (
        await _inbound(hooks, key, secret, _text(hooks.env.brand_id, "old secret " * 10))
    ).status_code == 202

    # Revocation: every secret stops at once; the row stays for the audit trail.
    assert (await _call(hooks, "DELETE", f"/webhooks/endpoints/{ep['id']}")).status_code == 204
    for s in (secret, new_secret):
        assert (
            await _inbound(hooks, key, s, _text(hooks.env.brand_id, "after revoke " * 10))
        ).status_code == 401
    listed = (await _call(hooks, "GET", "/webhooks/endpoints")).json()["items"]
    assert next(e for e in listed if e["id"] == ep["id"])["revoked_at"] is not None
    async with get_sessionmaker()() as session:
        actions = (
            (
                await session.execute(
                    select(AuditEvent.action)
                    .where(AuditEvent.entity_type == "webhook_endpoints")
                    .order_by(AuditEvent.occurred_at)
                )
            )
            .scalars()
            .all()
        )
    assert "webhook_endpoint.secret_rotated" in actions and "webhook_endpoint.revoked" in actions


async def test_inbound_campaign_generate_is_limited_to_its_brands(hooks: Hooks) -> None:
    other = await _other_brand(hooks)
    ep = await _endpoint(hooks, ["campaign.generate"], brand_ids=[other])
    campaign_id = await generated_campaign(hooks.env)  # belongs to env.brand_id, not `other`
    resp = await _inbound(
        hooks,
        ep["key"],
        ep["secret"],
        {"operation": "campaign.generate", "payload": {"campaign_id": campaign_id}},
    )
    assert resp.status_code == 404  # out-of-scope resources look like missing ones


async def test_inbound_campaign_generate_starts_planning_as_the_integration(hooks: Hooks) -> None:
    """Success path: an in-scope DRAFT campaign with a source moves to PLANNING (C2) with the
    INTEGRATION actor recorded; nothing is approved or published."""
    ep = await _endpoint(hooks, ["campaign.generate"])
    draft = await hooks.env.client.post(
        "/api/v1/campaigns/wizard", headers=hooks.env.headers, json=_wizard_draft(hooks.env)
    )
    assert draft.status_code in (201, 202), draft.text
    campaign_id = str(draft.json()["campaign"]["id"])
    assert draft.json()["campaign"]["status"] == "DRAFT"
    resp = await _inbound(
        hooks,
        ep["key"],
        ep["secret"],
        {"operation": "campaign.generate", "payload": {"campaign_id": campaign_id}},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["result_ref"]["campaign_id"] == campaign_id
    got = await hooks.env.client.get(f"/api/v1/campaigns/{campaign_id}", headers=hooks.env.headers)
    assert got.json()["status"] == "PLANNING"
    async with get_sessionmaker()() as session:
        actors = (
            await session.execute(
                select(AuditEvent.actor_type, AuditEvent.actor_id).where(
                    AuditEvent.entity_id == uuid.UUID(campaign_id),
                    AuditEvent.actor_id == f"integration:{ep['key']}",
                )
            )
        ).all()
    assert actors and {a.actor_type.value for a in actors} == {"INTEGRATION"}


# ------------------------------------------------------------------ outbound
async def _subscribe(
    h: Hooks, event_types: list[str], brand_ids: list[str] | None = None
) -> dict[str, Any]:
    resp = await _call(
        h,
        "POST",
        "/webhooks/subscriptions",
        json={
            "target_url": h.receiver.url,
            "brand_ids": brand_ids or [h.env.brand_id],
            "event_types": event_types,
        },
    )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def _verify(secret: str, received: Any) -> dict[str, Any]:
    ts = received.headers["x-omnigrowth-timestamp"]
    delivery = received.headers["x-omnigrowth-delivery-id"]
    mac = hmac.new(secret.encode(), f"{ts}.{delivery}.".encode() + received.body, hashlib.sha256)
    expected = "v1=" + mac.hexdigest()
    offered = received.headers["x-omnigrowth-signature"].split(",")
    assert any(hmac.compare_digest(expected, s.strip()) for s in offered)
    body: dict[str, Any] = json.loads(received.body)
    assert received.headers["x-omnigrowth-event-id"] == body["id"]
    return body


def _n8n_verify(secret: str, received: Any) -> dict[str, Any]:
    """Run n8n/code/verify-and-route.js (the example workflow's Code node) on a delivery."""
    import base64

    from tests.unit.test_n8n_workflows import _run

    case = {
        "env": {"OMNIGROWTH_WEBHOOK_SECRET": secret},
        "item": {
            "json": {"headers": received.headers},
            "binary": {"data": {"data": base64.b64encode(received.body).decode()}},
        },
    }
    return _run("verify-and-route.js", [case])[0]


async def _dispatch(now: datetime | None = None) -> dict[str, int]:
    return await wd.dispatch_once(get_sessionmaker(), now=now)


async def test_approval_events_reach_only_matching_subscriptions_with_minimal_signed_payloads(
    hooks: Hooks,
) -> None:
    other = await _other_brand(hooks)
    sub = await _subscribe(hooks, ["content.approval_requested"])
    elsewhere = await _subscribe(hooks, ["content.approval_requested"], brand_ids=[other])
    campaign_id = await generated_campaign(hooks.env)
    queue = (
        await _call(
            hooks, "GET", "/approvals", hooks.env.headers, params={"campaign_id": campaign_id}
        )
    ).json()["items"]
    assert queue
    # The shared test database also holds other tests' events and subscriptions; keep
    # dispatching (bounded) until this subscription's deliveries are done.
    for _ in range(20):
        await _dispatch()
        if len(hooks.receiver.received) >= len(queue):
            break
    bodies = [_verify(sub["secret"], r) for r in hooks.receiver.received]
    assert len(bodies) == len(queue)
    assert {b["data"]["content_asset_id"] for b in bodies} == {a["id"] for a in queue}
    for b in bodies:
        assert b["type"] == "content.approval_requested" and b["brand_id"] == hooks.env.brand_id
        assert set(b["data"]) == {
            "content_asset_id",
            "content_version_id",
            "campaign_id",
            "platform",
            "format",
            "status",
        }
        assert (
            b["links"]["content"]
            == f"http://127.0.0.1:5173/content/{b['data']['content_asset_id']}"
        )
        text_of_asset = next(a for a in queue if a["id"] == b["data"]["content_asset_id"])[
            "current_version"
        ]["body"]
        assert text_of_asset not in json.dumps(b)  # no content in notifications
    # The example n8n workflow's own verification code accepts a real delivery.
    first = hooks.receiver.received[0]
    routed = _n8n_verify(sub["secret"], first)
    assert (routed["httpStatus"], routed["route"]) == (200, 0)
    assert "needs human approval" in routed["message"]
    async with get_sessionmaker()() as session:
        other_rows = (
            (
                await session.execute(
                    select(WebhookDelivery).where(
                        WebhookDelivery.subscription_id == uuid.UUID(elsewhere["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert other_rows == []  # brand isolation
    # Delivery changed nothing in OmniGrowth: the content still waits for a person.
    again = (
        await _call(
            hooks, "GET", "/approvals", hooks.env.headers, params={"campaign_id": campaign_id}
        )
    ).json()["items"]
    assert {a["id"] for a in again} == {a["id"] for a in queue}


async def test_retries_duplicates_exhaustion_downtime_recovery_and_revocation(hooks: Hooks) -> None:
    sub = await _subscribe(hooks, ["integration.ping"])

    # A 500 is retried later with the SAME event id (receivers deduplicate on it).
    hooks.receiver.replies.extend([500])
    ping = (await _call(hooks, "POST", f"/webhooks/subscriptions/{sub['id']}/test")).json()
    now = datetime.now(UTC)
    await _dispatch(now)
    await _dispatch(now + timedelta(minutes=5))
    first, second = hooks.receiver.received[-2:]
    assert (
        first.headers["x-omnigrowth-event-id"]
        == second.headers["x-omnigrowth-event-id"]
        == ping["event_id"]
    )
    assert (first.headers["x-omnigrowth-attempt"], second.headers["x-omnigrowth-attempt"]) == (
        "1",
        "2",
    )
    delivered = (
        await _call(hooks, "GET", "/webhooks/deliveries", params={"subscription_id": sub["id"]})
    ).json()["items"]
    assert delivered[0]["status"] == "SUCCEEDED" and delivered[0]["attempt_count"] == 2

    # Receiver down: bounded attempts, then FAILED with an inspectable reason.
    port = hooks.receiver.port
    hooks.receiver.stop()
    ping = (await _call(hooks, "POST", f"/webhooks/subscriptions/{sub['id']}/test")).json()
    now = datetime.now(UTC)
    for step in range(4):
        await _dispatch(now + timedelta(hours=1 + step * 10))
    failed = (
        await _call(hooks, "GET", "/webhooks/deliveries", params={"status": "FAILED"})
    ).json()["items"]
    row = next(d for d in failed if d["event_id"] == ping["event_id"])
    assert row["attempt_count"] == 3 and row["max_attempts"] == 3
    assert row["last_error"] and "127.0.0.1" not in row["last_error"]  # no URL in stored errors

    # The core flow does not wait for n8n: approving still works while the receiver is down.
    campaign_id = await generated_campaign(hooks.env)
    approved = await approve_all(hooks.env, campaign_id)
    assert approved

    # Recovery: the receiver is back; an owner redelivers.
    hooks.receiver = Receiver(port=port).start()
    redo = await _call(hooks, "POST", f"/webhooks/deliveries/{row['id']}/redeliver")
    assert redo.status_code == 200 and redo.json()["status"] == "PENDING"
    await _dispatch(datetime.now(UTC))
    assert any(
        r.headers["x-omnigrowth-event-id"] == ping["event_id"] for r in hooks.receiver.received
    )
    done = (
        await _call(hooks, "GET", "/webhooks/deliveries", params={"subscription_id": sub["id"]})
    ).json()["items"]
    assert next(d for d in done if d["id"] == row["id"])["status"] == "SUCCEEDED"

    # Revocation: queued deliveries fail without being sent; no new test events.
    ping = (await _call(hooks, "POST", f"/webhooks/subscriptions/{sub['id']}/test")).json()
    assert (await _call(hooks, "DELETE", f"/webhooks/subscriptions/{sub['id']}")).status_code == 204
    before = len(hooks.receiver.received)
    await _dispatch(datetime.now(UTC))
    assert len(hooks.receiver.received) == before
    revoked = (
        await _call(hooks, "GET", "/webhooks/deliveries", params={"subscription_id": sub["id"]})
    ).json()["items"]
    assert (
        next(d for d in revoked if d["event_id"] == ping["event_id"])["last_error"]
        == "subscription revoked"
    )
    assert (
        await _call(hooks, "POST", f"/webhooks/subscriptions/{sub['id']}/test")
    ).status_code == 409


async def test_publication_failure_and_uncertainty_events_say_whether_they_were_simulated(
    hooks: Hooks,
) -> None:
    sub = await _subscribe(hooks, ["publication.failed", "publication.uncertain"])
    campaign_id = await generated_campaign(hooks.env)
    asset = (await approve_all(hooks.env, campaign_id))[0]
    accounts = await platform_accounts(hooks.env, asset["platform"])
    scheduled = await hooks.env.client.post(
        f"/api/v1/approvals/{asset['id']}/actions",
        headers=hooks.env.headers,
        json={
            "action": "SCHEDULE",
            "content_version_id": asset["approved_version_id"],
            "platform_account_id": accounts[asset["platform"]],
            "scheduled_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert scheduled.status_code == 200, scheduled.text
    job_id = scheduled.json()["publication_job"]["id"]
    # Drive the job to UNCERTAIN then FAILED at the database level to exercise the event
    # triggers exactly as the publisher's state changes would (MOCK = simulated).
    async with get_sessionmaker()() as session:
        for status in ("UNCERTAIN", "FAILED"):
            await session.execute(
                text(
                    "UPDATE publication_jobs SET status = :s, publish_mode = 'MOCK' WHERE id = :id"
                ),
                {"s": status, "id": job_id},
            )
        await session.commit()
        events = (
            (
                await session.execute(
                    select(IntegrationEvent)
                    .where(IntegrationEvent.subject_id == uuid.UUID(job_id))
                    .order_by(IntegrationEvent.occurred_at)
                )
            )
            .scalars()
            .all()
        )
    # Both changes commit together (same occurred_at): compare without relying on order.
    assert sorted(e.event_type for e in events) == ["publication.failed", "publication.uncertain"]
    for _ in range(20):
        await _dispatch()
        if len(hooks.receiver.received) >= 2:
            break
    bodies = [_verify(sub["secret"], r) for r in hooks.receiver.received]
    assert {b["type"] for b in bodies} == {"publication.uncertain", "publication.failed"}
    for b in bodies:
        assert b["data"]["simulated"] is True and b["data"]["publish_mode"] == "MOCK"
        assert b["data"]["publication_job_id"] == job_id
        assert b["links"]["publishing"] == "http://127.0.0.1:5173/publications"


# ------------------------------------------------------------------ review regressions
async def test_auto_approved_content_is_not_announced_as_needing_approval(hooks: Hooks) -> None:
    """Review MAJOR 4: with brand auto-approve the asset passes AWAITING_APPROVAL in one
    transaction; the recorded event is consumed without telling anyone to approve it."""
    patched = await _call(
        hooks, "PATCH", f"/brands/{hooks.env.brand_id}", json={"auto_approve_enabled": True}
    )
    assert patched.status_code == 200, patched.text
    sub = await _subscribe(hooks, ["content.approval_requested"])
    campaign_id = await generated_campaign(hooks.env)
    for _ in range(20):
        counts = await _dispatch()
        if counts["fanned_out"] == 0 and counts["claimed"] == 0:
            break
    async with get_sessionmaker()() as session:
        rows = (
            (
                await session.execute(
                    select(WebhookDelivery).where(
                        WebhookDelivery.subscription_id == uuid.UUID(sub["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        recorded = (
            await session.execute(
                select(IntegrationEvent.fanned_out_at).where(
                    IntegrationEvent.event_type == "content.approval_requested",
                    IntegrationEvent.brand_id == uuid.UUID(hooks.env.brand_id),
                )
            )
        ).all()
    assert recorded and all(r.fanned_out_at is not None for r in recorded)  # consumed
    assert rows == [] and hooks.receiver.received == []
    queue = (
        await _call(
            hooks, "GET", "/approvals", hooks.env.headers, params={"campaign_id": campaign_id}
        )
    ).json()["items"]
    assert queue == []


async def test_outbound_rotation_signs_with_both_secrets_during_the_grace_period(
    hooks: Hooks,
) -> None:
    sub = await _subscribe(hooks, ["integration.ping"])
    rotated = await _call(hooks, "POST", f"/webhooks/subscriptions/{sub['id']}/rotate-secret")
    assert rotated.status_code == 200, rotated.text
    new_secret = rotated.json()["secret"]
    ping = (await _call(hooks, "POST", f"/webhooks/subscriptions/{sub['id']}/test")).json()
    await _dispatch(datetime.now(UTC))
    got = next(
        r for r in hooks.receiver.received if r.headers["x-omnigrowth-event-id"] == ping["event_id"]
    )
    assert len(got.headers["x-omnigrowth-signature"].split(",")) == 2
    for secret in (sub["secret"], new_secret):  # receivers on either secret accept it
        assert _verify(secret, got)["id"] == ping["event_id"]
        assert _n8n_verify(secret, got)["httpStatus"] == 200
    assert (
        await _call(hooks, "POST", f"/webhooks/subscriptions/{sub['id']}/rotate-secret")
    ).status_code == 409


async def test_a_last_attempt_whose_sender_died_ends_failed_not_stuck(hooks: Hooks) -> None:
    """Review MAJOR 5: DELIVERING + expired lease + no attempts left -> FAILED (inspectable)."""
    sub = await _subscribe(hooks, ["integration.ping"])
    ping = (await _call(hooks, "POST", f"/webhooks/subscriptions/{sub['id']}/test")).json()
    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE webhook_deliveries SET state = 'DELIVERING', attempt_count = max_attempts,"
                " lease_until = now() - interval '1 minute' WHERE id = :id"
            ),
            {"id": ping["id"]},
        )
        await session.commit()
    await _dispatch(datetime.now(UTC))
    rows = (
        await _call(hooks, "GET", "/webhooks/deliveries", params={"subscription_id": sub["id"]})
    ).json()["items"]
    row = next(d for d in rows if d["id"] == ping["id"])
    assert row["status"] == "FAILED"
    assert row["last_error"] == "lease expired on the last attempt (sender stopped)"
    assert hooks.receiver.received == []
    # An owner can still send it again.
    redo = await _call(hooks, "POST", f"/webhooks/deliveries/{ping['id']}/redeliver")
    assert redo.status_code == 200 and redo.json()["status"] == "PENDING"


async def test_a_slow_sender_cannot_overwrite_a_newer_attempt(hooks: Hooks) -> None:
    """Review MINOR 3: if the lease expired mid-send and another sender re-claimed the row,
    the first sender's result is discarded (fenced on the attempt number)."""
    sub = await _subscribe(hooks, ["integration.ping"])
    ping = (await _call(hooks, "POST", f"/webhooks/subscriptions/{sub['id']}/test")).json()
    factory = get_sessionmaker()
    async with factory() as session:
        claimed = [
            c for c in await wd.claim_due(session, datetime.now(UTC)) if str(c.id) == ping["id"]
        ]
    assert len(claimed) == 1

    async def reclaimed_meanwhile(*_: Any, **__: Any) -> int:
        async with factory() as other:
            await other.execute(
                text(
                    "UPDATE webhook_deliveries SET attempt_count = attempt_count + 1 WHERE id = :id"
                ),
                {"id": ping["id"]},
            )
            await other.commit()
        return 200

    async with factory() as session:
        result = await wd.deliver(session, claimed[0], send=reclaimed_meanwhile)
    assert result == "SUPERSEDED"
    rows = (
        await _call(hooks, "GET", "/webhooks/deliveries", params={"subscription_id": sub["id"]})
    ).json()["items"]
    row = next(d for d in rows if d["id"] == ping["id"])
    assert row["status"] == "DELIVERING" and row["attempt_count"] == 2  # the newer owner's
