"""Phase 13 gate: the Mastodon live connector end to end against a LOCAL fake instance.

No real platform is contacted: the instance is tests/integration/fake_mastodon.py on
127.0.0.1 (allowlisted), and the token is generated per test. These tests run the real
dispatcher, vault, SSRF-guarded HTTP client and adapter. Because A6 makes LIVE impossible
under ENV=test (proven separately in test_publishing_guards / settings), the live-path
tests switch ENV to dev for the dispatch against the local fake instance only.

Covers: owner-only write-only encrypted credentials (rotation, revocation, audit), the live
switch phrase, every live guard refusing before anything is sent, brand isolation, a
human-approved thread published exactly once with SANDBOX evidence, ambiguous outcomes held
UNCERTAIN and reconciled by lookup without re-posting, the confirmed-not-published event,
rate limits and connection failures with recovery, live metrics and evidence labelling.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from omnigrowth.core.enums import Environment, PublishMode, UserRole
from omnigrowth.core.settings import get_settings
from omnigrowth.models import AuditEvent, PlatformCredential
from omnigrowth.models.integrations import IntegrationEvent
from omnigrowth.publishing.config import cached_publishing_config
from omnigrowth.publishing.mock import MockPublisher
from omnigrowth.publishing.registry import live_publisher_for
from omnigrowth.publishing.scheduler import TickResult, run_once
from omnigrowth.services.analytics import pull_campaign_metrics
from omnigrowth.storage.db import get_sessionmaker
from tests.integration.campaign_support import TZ, Env
from tests.integration.fake_mastodon import FakeMastodon
from tests.integration.helpers import auth_headers
from tests.integration.test_repurposing import _derive, _root

pytestmark = pytest.mark.integration

SCOPES = ["read:accounts", "read:statuses", "write:statuses"]
PHRASE = "ENABLE LIVE PUBLISHING FOR MASTODON"


class Live:
    env: Env
    owner: dict[str, str]
    fake: FakeMastodon
    token: str
    monkeypatch: pytest.MonkeyPatch


@pytest.fixture
async def live(campaign_env: Env, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Live]:
    h = Live()
    h.token = "fake_" + secrets.token_urlsafe(24)  # generated per test, never a real token
    h.fake = FakeMastodon(h.token).start()
    kek = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    monkeypatch.setenv("OMNIGROWTH_CREDENTIAL_KEK", kek)
    monkeypatch.setenv("OMNIGROWTH_PLATFORM_HOST_ALLOWLIST", f"127.0.0.1:{h.fake.port}")
    get_settings.cache_clear()
    h.env, h.monkeypatch = campaign_env, monkeypatch
    h.owner = await auth_headers(campaign_env.client, UserRole.OWNER)
    try:
        yield h
    finally:
        h.fake.stop()
        get_settings.cache_clear()


def _mode(h: Live, live_mode: bool) -> None:
    """LIVE dispatch against the local fake instance needs ENV != test (A6)."""
    h.monkeypatch.setenv("ENV", "dev" if live_mode else "test")
    h.monkeypatch.setenv("PUBLISH_MODE", "LIVE" if live_mode else "MOCK")
    get_settings.cache_clear()


async def _call(
    h: Live, method: str, path: str, headers: dict[str, str] | None = None, **kw: Any
) -> Any:
    return await h.env.client.request(method, f"/api/v1{path}", headers=headers or h.owner, **kw)


async def _account(h: Live, *, sandbox: bool = True, brand_id: str | None = None) -> dict[str, Any]:
    resp = await _call(
        h,
        "POST",
        "/platform-accounts",
        json={
            "brand_id": brand_id or h.env.brand_id,
            "platform": "MASTODON",
            "account_type": "INSTANCE_USER",
            "handle": f"@brand{secrets.token_hex(3)}@127.0.0.1",
            "timezone": TZ,
            "settings": {
                "instance_base_url": h.fake.base_url,
                "visibility": "unlisted",
                "max_characters": 500,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    account = dict(resp.json())
    if sandbox:
        patched = await _call(
            h, "PATCH", f"/platform-accounts/{account['id']}", json={"sandbox": True}
        )
        assert patched.status_code == 200, patched.text
        account = dict(patched.json())
    return account


async def _store_token(h: Live, account_id: str, token: str | None = None) -> Any:
    return await _call(
        h,
        "POST",
        f"/platform-accounts/{account_id}/credentials",
        json={"kind": "OAUTH2", "secret": token or h.token, "scopes": SCOPES},
    )


async def _enable(h: Live, account_id: str) -> Any:
    """Enabling live is refused under ENV=test; switch to dev (still MOCK) to enable."""
    if os.environ.get("ENV") == "test":
        h.monkeypatch.setenv("ENV", "dev")
        get_settings.cache_clear()
    return await _call(
        h,
        "POST",
        f"/platform-accounts/{account_id}/live",
        json={"enabled": True, "confirmation": PHRASE},
    )


async def _ready_account(h: Live, *, sandbox: bool = True) -> dict[str, Any]:
    account = await _account(h, sandbox=sandbox)
    assert (await _store_token(h, account["id"])).status_code == 201
    enabled = await _enable(h, account["id"])
    assert enabled.status_code == 200, enabled.text
    return dict(enabled.json())


async def _approved_thread(h: Live) -> dict[str, Any]:
    """A Mastodon THREAD (three posts) approved by a person (EDITOR)."""
    root = await _root(h.env)
    child = await _derive(h.env, root["id"], "MASTODON", "THREAD")
    resp = await h.env.client.post(
        f"/api/v1/approvals/{child['id']}/actions",
        headers=h.env.headers,
        json={"action": "APPROVE", "content_version_id": child["current_version_id"]},
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["asset"])


async def _publish_now(h: Live, asset: dict[str, Any], account_id: str) -> dict[str, Any]:
    resp = await h.env.client.post(
        f"/api/v1/approvals/{asset['id']}/actions",
        headers=h.env.headers,
        json={
            "action": "PUBLISH_NOW",
            "content_version_id": asset["approved_version_id"],
            "platform_account_id": account_id,
        },
    )
    assert resp.status_code in (200, 202), resp.text
    return dict(resp.json()["publication_job"])


async def _tick(now: datetime | None = None) -> TickResult:
    return await run_once(
        publisher=MockPublisher(),
        config=cached_publishing_config(get_settings().config_dir),
        now=now or datetime.now(UTC),
        live_factory=live_publisher_for,
    )


async def _job(h: Live, job_id: str) -> dict[str, Any]:
    return dict((await _call(h, "GET", f"/publications/jobs/{job_id}", h.env.headers)).json())


async def _publication_of(h: Live, job_id: str) -> dict[str, Any]:
    items = (await _call(h, "GET", "/publications", h.env.headers)).json()["items"]
    return next(p for p in items if p["publication_job_id"] == job_id)


async def _audit_dump() -> str:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(AuditEvent))).scalars().all()
    return json.dumps(
        [[r.action, r.actor_id, r.before, r.after, r.metadata_] for r in rows], default=str
    )


# ------------------------------------------------------------------ credentials & switch
async def test_credentials_are_owner_only_write_only_encrypted_rotated_and_audited(
    live: Live,
) -> None:
    account = await _account(live)
    aid = account["id"]
    for role in (UserRole.EDITOR, UserRole.VIEWER):
        headers = await auth_headers(live.env.client, role)
        assert (
            await _call(live, "GET", f"/platform-accounts/{aid}/credentials", headers)
        ).status_code == 403
        denied = await _call(
            live,
            "POST",
            f"/platform-accounts/{aid}/credentials",
            headers,
            json={"kind": "OAUTH2", "secret": live.token, "scopes": SCOPES},
        )
        assert denied.status_code == 403
    # Wrong kind, missing scopes, malformed secret: refused, and the secret is never echoed.
    for body, field in (
        ({"kind": "API_KEY", "secret": live.token, "scopes": SCOPES}, "kind"),
        ({"kind": "OAUTH2", "secret": live.token, "scopes": ["read"]}, "scopes"),
        ({"kind": "OAUTH2", "secret": live.token + " x", "scopes": SCOPES}, None),
    ):
        resp = await _call(live, "POST", f"/platform-accounts/{aid}/credentials", json=body)
        assert resp.status_code == 422, resp.text
        assert live.token not in resp.text
        if field == "scopes":  # "read" covers read:*; write:statuses is missing
            assert resp.json()["details"]["missing"] == ["write:statuses"]
    # Enabling live is impossible in a test environment, whatever else is ready.
    in_test = await _call(
        live,
        "POST",
        f"/platform-accounts/{aid}/live",
        json={"enabled": True, "confirmation": PHRASE},
    )
    assert in_test.status_code == 403 and "ENV_TEST" in in_test.json()["details"]["blockers"]
    # Outside it, enabling without a credential lists what blocks it.
    blocked = await _enable(live, aid)
    assert blocked.status_code == 403 and "NO_CREDENTIAL" in blocked.json()["details"]["blockers"]
    live.monkeypatch.setenv("ENV", "test")
    get_settings.cache_clear()

    stored = await _store_token(live, aid)
    assert stored.status_code == 201, stored.text
    first = stored.json()
    assert live.token not in stored.text and "secret" not in first
    assert (
        first["fingerprint"] and first["kek_id"].startswith("kek-") and first["revoked_at"] is None
    )
    async with get_sessionmaker()() as session:
        row = await session.get(PlatformCredential, uuid.UUID(first["id"]))
        assert row is not None and live.token.encode() not in row.ciphertext + row.wrapped_dek

    # Rotation revokes the previous credential in the same step.
    second = (await _store_token(live, aid, token="fake_" + secrets.token_urlsafe(24))).json()
    listed = (await _call(live, "GET", f"/platform-accounts/{aid}/credentials")).json()["items"]
    by_id = {c["id"]: c for c in listed}
    assert (
        by_id[first["id"]]["revoked_at"] is not None and by_id[second["id"]]["revoked_at"] is None
    )
    assert live.token not in json.dumps(listed)
    async with get_sessionmaker()() as session:
        old = await session.get(PlatformCredential, uuid.UUID(first["id"]))
        assert old is not None and old.ciphertext == b"" and old.wrapped_dek == b""  # destroyed

    view = (await _call(live, "GET", f"/platform-accounts/{aid}")).json()
    assert view["has_credential"] is True and view["live_connector_available"] is True
    assert {"PUBLISH_MODE_NOT_LIVE", "ENV_TEST", "ACCOUNT_NOT_LIVE_ENABLED"} <= set(
        view["live_blockers"]
    )

    # The live switch needs the exact phrase; the sandbox flag cannot move under a live account.
    wrong = await _call(
        live,
        "POST",
        f"/platform-accounts/{aid}/live",
        json={"enabled": True, "confirmation": "yes"},
    )
    assert wrong.status_code == 422 and wrong.json()["details"]["expected"] == PHRASE
    on = await _enable(live, aid)
    assert on.status_code == 200 and on.json()["live_enabled"] is True
    moved = await _call(live, "PATCH", f"/platform-accounts/{aid}", json={"sandbox": False})
    assert moved.status_code == 409
    # Revoking the credential switches live publishing off (audited).
    revoked = await _call(live, "DELETE", f"/platform-accounts/{aid}/credentials/{second['id']}")
    assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None
    assert (await _call(live, "GET", f"/platform-accounts/{aid}")).json()["live_enabled"] is False

    audit = await _audit_dump()
    for action in (
        "credential.stored",
        "credential.rotated",
        "credential.revoked",
        "platform_account.live_enabled.set",
    ):
        assert action in audit
    assert live.token not in audit

    # A platform without a verified connector takes no credential; the catalog says so.
    linkedin = await _call(
        live,
        "POST",
        "/platform-accounts",
        json={
            "brand_id": live.env.brand_id,
            "platform": "LINKEDIN",
            "account_type": "ORGANIZATION",
            "handle": f"li-{secrets.token_hex(3)}",
        },
    )
    refused = await _store_token(live, linkedin.json()["id"])
    assert (
        refused.status_code == 422 and refused.json()["details"]["reason"] == "NO_VERIFIED_ADAPTER"
    )
    catalog = {p["platform"]: p for p in (await _call(live, "GET", "/platforms")).json()["items"]}
    assert catalog["MASTODON"]["capability_status"] == "VERIFIED"
    assert catalog["MASTODON"]["live_publishing_available"] is True
    assert catalog["LINKEDIN"]["capability_status"] == "UNVERIFIED"
    assert live.fake.requests == []  # nothing here ever called the platform


# ------------------------------------------------------------------ the live path
async def test_a_human_approved_thread_is_published_live_once_with_sandbox_evidence(
    live: Live, caplog: pytest.LogCaptureFixture
) -> None:
    asset = await _approved_thread(live)  # before the account: writer fixtures (A8)
    account = await _ready_account(live, sandbox=True)
    _mode(live, live_mode=True)
    job = await _publish_now(live, asset, account["id"])

    preview = (
        await _call(live, "GET", f"/publications/jobs/{job['id']}/preview", live.env.headers)
    ).json()
    assert preview["blockers"] == [] and preview["simulated"] is False
    assert preview["evidence_class"] == "SANDBOX" and preview["publish_mode"] == "LIVE"
    assert (
        preview["details"]["visibility"] == "unlisted" and len(preview["details"]["segments"]) == 3
    )
    assert preview["details"]["media_attached"] == [] and live.token not in json.dumps(preview)
    assert live.fake.posts == []  # a preview sends nothing

    with caplog.at_level(logging.DEBUG):
        result = await _tick()
    assert result.succeeded == 1, result
    posts = live.fake.posts
    assert len(posts) == 3
    assert [p["idempotency_key"][-2:] for p in posts] == ["-0", "-1", "-2"]
    assert [p["body"]["status"] for p in posts] == preview["details"]["segments"]
    assert posts[0]["body"].get("in_reply_to_id") is None
    ids = sorted(live.fake.statuses, key=int)
    assert [p["body"].get("in_reply_to_id") for p in posts[1:]] == ids[:2]
    assert all(p["body"]["visibility"] == "unlisted" for p in posts)

    pub = await _publication_of(live, job["id"])
    assert pub["status"] == "PUBLISHED" and pub["publish_mode"] == "LIVE"
    assert pub["evidence_class"] == "SANDBOX" and pub["external_post_id"] == ids[0]
    assert pub["canonical_url"].endswith(f"/@brand/{ids[0]}")
    content = (await _call(live, "GET", f"/content/{asset['id']}", live.env.headers)).json()
    assert content["status"] == "PUBLISHED"

    again = await _tick(datetime.now(UTC) + timedelta(hours=1))
    assert again.claimed == 0 and len(live.fake.posts) == 3  # exactly once

    # Metrics come from the platform (API) but a sandbox post is never real evidence.
    async with get_sessionmaker()() as session:
        pulled = await pull_campaign_metrics(session, uuid.UUID(asset["campaign_id"]))
    assert pulled["snapshots"], pulled
    metrics = (
        await _call(live, "GET", f"/analytics/publications/{pub['id']}/metrics", live.env.headers)
    ).json()
    assert metrics["raw"]["source"] == "API"
    assert (metrics["raw"]["likes"], metrics["raw"]["shares"], metrics["raw"]["comments"]) == (
        5,
        3,
        1,
    )
    assert metrics["raw"]["impressions"] is None
    assert metrics["provenance"]["sandbox"] is True and metrics["provenance"]["real"] is False

    # The token appears nowhere: logs, audit, API views.
    assert all(live.token not in r.getMessage() for r in caplog.records)
    assert live.token not in await _audit_dump()
    assert live.token not in json.dumps(await _job(live, job["id"]))


async def test_live_guards_refuse_before_anything_is_sent(live: Live) -> None:
    asset = await _approved_thread(live)
    account = await _ready_account(live, sandbox=True)
    aid = account["id"]
    kek = os.environ["OMNIGROWTH_CREDENTIAL_KEK"]

    async def refused(expected: str) -> None:
        _mode(live, live_mode=False)  # schedule (MOCK), dispatch LIVE
        job = await _publish_now(live, asset, aid)
        _mode(live, live_mode=True)
        result = await _tick()
        assert result.failed == 1, result
        got = await _job(live, job["id"])
        assert got["status"] == "FAILED" and got["last_error_code"] == "LIVE_PUBLISH_BLOCKED"
        assert got["last_error"]["guard"] == expected
        assert live.fake.posts == []

    # 0. A6: with everything else ready (live account, credential, verified adapter),
    #    ENV=test still blocks. Settings refuse LIVE+test, so force it in-process to prove
    #    the dispatcher's own re-check.
    _mode(live, live_mode=False)
    job = await _publish_now(live, asset, aid)
    forced = get_settings().model_copy(
        update={"publish_mode": PublishMode.LIVE, "env": Environment.TEST}
    )
    live.monkeypatch.setattr("omnigrowth.publishing.scheduler.get_settings", lambda: forced)
    assert (await _tick()).failed == 1
    got = await _job(live, job["id"])
    assert got["last_error"]["guard"] == "ENV_TEST" and live.fake.posts == []
    live.monkeypatch.undo()  # also restores env; re-apply what this test needs
    live.monkeypatch.setenv("OMNIGROWTH_CREDENTIAL_KEK", kek)
    live.monkeypatch.setenv("OMNIGROWTH_PLATFORM_HOST_ALLOWLIST", f"127.0.0.1:{live.fake.port}")
    get_settings.cache_clear()

    # 1. The account is not live-enabled.
    off = await _call(live, "POST", f"/platform-accounts/{aid}/live", json={"enabled": False})
    assert off.status_code == 200
    await refused("ACCOUNT_NOT_LIVE_ENABLED")
    # 2. No credential (revoked; the flag forced back on to reach the next guard).
    creds = (await _call(live, "GET", f"/platform-accounts/{aid}/credentials")).json()["items"]
    await _call(live, "DELETE", f"/platform-accounts/{aid}/credentials/{creds[0]['id']}")
    async with get_sessionmaker()() as session:
        await session.execute(
            text("UPDATE platform_accounts SET live_enabled = true WHERE id = :id"), {"id": aid}
        )
        await session.commit()
    await refused("NO_CREDENTIAL")
    assert (await _store_token(live, aid)).status_code == 201
    # 3. The latest approval of this version is automatic (SYSTEM): not enough for LIVE (Q6).
    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "INSERT INTO approval_actions (id, brand_id, content_asset_id, content_version_id, "
                "action, from_status, to_status, actor_type, actor_id, correlation_id, created_at) "
                "VALUES (:id, :brand, :asset, :version, 'APPROVE', 'AWAITING_APPROVAL', "
                "'APPROVED', 'SYSTEM', 'auto-approve', 'test', now() + interval '1 second')"
            ),
            {
                "id": uuid.uuid4(),
                "brand": live.env.brand_id,
                "asset": asset["id"],
                "version": asset["approved_version_id"],
            },
        )
        await session.commit()
    await refused("NOT_HUMAN_APPROVED")


async def test_brand_isolation_a_job_never_posts_with_another_brands_account(live: Live) -> None:
    asset = await _approved_thread(live)
    mine = await _ready_account(live, sandbox=True)
    other_brand = await _call(
        live,
        "POST",
        "/brands",
        json={
            "slug": f"other-{secrets.token_hex(3)}",
            "name": "Other",
            "display_name": "Other",
            "timezone": TZ,
        },
    )
    theirs = await _account(live, sandbox=True, brand_id=other_brand.json()["id"])
    assert (await _store_token(live, theirs["id"])).status_code == 201
    assert (await _enable(live, theirs["id"])).status_code == 200
    _mode(live, live_mode=True)
    job = await _publish_now(live, asset, mine["id"])
    # Even if a job row pointed at another brand's account, the dispatcher refuses it.
    async with get_sessionmaker()() as session:
        await session.execute(
            text("UPDATE publication_jobs SET platform_account_id = :acc WHERE id = :id"),
            {"acc": theirs["id"], "id": job["id"]},
        )
        await session.commit()
    result = await _tick()
    assert result.failed == 1
    got = await _job(live, job["id"])
    assert got["last_error"]["guard"] == "BRAND_MISMATCH" and live.fake.posts == []


# ------------------------------------------------------------------ uncertainty & reconciliation
async def test_an_ambiguous_outcome_is_held_and_found_by_lookup_without_reposting(
    live: Live,
) -> None:
    asset = await _approved_thread(live)
    account = await _ready_account(live, sandbox=False)
    _mode(live, live_mode=True)
    job = await _publish_now(live, asset, account["id"])
    live.fake.plan.extend(["ok", "ok", "create_then:500"])  # the last answer is lost
    result = await _tick()
    assert result.uncertain == 1, result
    assert len(live.fake.statuses) == 3
    pub = await _publication_of(live, job["id"])
    assert pub["status"] == "PUBLICATION_UNCERTAIN" and pub["evidence_class"] == "REAL"
    # Never retried automatically, and the account is held.
    later = await _tick(datetime.now(UTC) + timedelta(hours=2))
    assert later.claimed == 0 and len(live.fake.posts) == 3

    found = await _call(live, "POST", f"/publications/{pub['id']}/lookup", live.env.headers)
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["status"] == "FOUND" and len(body["found_post_ids"]) == 3
    assert body["publication"]["status"] == "PUBLISHED"
    assert body["publication"]["external_post_id"] == sorted(live.fake.statuses, key=int)[0]
    assert len(live.fake.posts) == 3  # the lookup only read
    assert all(r.startswith("GET") for r in live.fake.requests[3:])
    audit = await _audit_dump()
    assert "publication.lookup" in audit and "platform-lookup" in audit


async def test_confirmed_not_published_emits_its_own_event_and_never_retries(live: Live) -> None:
    asset = await _approved_thread(live)
    account = await _ready_account(live, sandbox=False)
    _mode(live, live_mode=True)
    job = await _publish_now(live, asset, account["id"])
    live.fake.plan.append("reject:502")  # a gateway error: nothing created, but unknowable
    assert (await _tick()).uncertain == 1
    pub = await _publication_of(live, job["id"])

    lookup = (
        await _call(live, "POST", f"/publications/{pub['id']}/lookup", live.env.headers)
    ).json()
    assert (
        lookup["status"] == "NOT_FOUND"
        and lookup["publication"]["status"] == "PUBLICATION_UNCERTAIN"
    )

    # Only an OWNER may state it was not published.
    editor_try = await _call(
        live,
        "POST",
        f"/publications/{pub['id']}/reconcile",
        live.env.headers,
        json={"method": "MANUAL_CONFIRM_NOT_PUBLISHED", "note": "checked the profile"},
    )
    assert editor_try.status_code == 403
    done = await _call(
        live,
        "POST",
        f"/publications/{pub['id']}/reconcile",
        json={
            "method": "MANUAL_CONFIRM_NOT_PUBLISHED",
            "note": "checked the profile: nothing there",
        },
    )
    assert done.status_code == 200 and done.json()["status"] == "FAILED"

    async with get_sessionmaker()() as session:
        events = (
            (
                await session.execute(
                    select(IntegrationEvent).where(
                        IntegrationEvent.subject_id == uuid.UUID(job["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
    types = sorted(e.event_type for e in events)
    assert types == ["publication.not_published_confirmed", "publication.uncertain"]
    confirmed = next(e for e in events if e.event_type == "publication.not_published_confirmed")
    assert confirmed.data["will_retry"] is False and confirmed.data["evidence_class"] == "REAL"
    assert "publication.reconciled_not_published" in await _audit_dump()

    # Nothing is retried: no POST, no new job, the asset is back to APPROVED for a person.
    before = len(live.fake.posts)
    assert (await _tick(datetime.now(UTC) + timedelta(days=1))).claimed == 0
    assert len(live.fake.posts) == before == 1
    jobs = (await _call(live, "GET", "/publications/jobs", live.env.headers)).json()["items"]
    assert [j["id"] for j in jobs if j["content_asset_id"] == asset["id"]] == [job["id"]]
    content = (await _call(live, "GET", f"/content/{asset['id']}", live.env.headers)).json()
    assert content["status"] == "APPROVED"


# ------------------------------------------------------------------ rate limits & recovery
async def test_connection_failures_and_rate_limits_are_retried_safely(live: Live) -> None:
    asset = await _approved_thread(live)
    account = await _ready_account(live, sandbox=True)
    _mode(live, live_mode=True)
    job = await _publish_now(live, asset, account["id"])

    port = live.fake.port
    live.fake.stop()  # the instance is down: nothing can be sent
    first = await _tick()
    assert first.retry_wait == 1, first
    got = await _job(live, job["id"])
    assert got["status"] == "RETRY_WAIT" and got["last_error_code"] == "PLATFORM_TIMEOUT"

    restarted = FakeMastodon(live.token, port=port).start()
    live.fake = restarted
    restarted.plan.append("rate_limit")
    second = await _tick(datetime.fromisoformat(got["due_at"]) + timedelta(seconds=1))
    assert second.retry_wait == 1, second
    got = await _job(live, job["id"])
    assert got["last_error_code"] == "PLATFORM_RATE_LIMIT" and restarted.statuses == {}
    due = datetime.fromisoformat(got["due_at"])
    assert due >= datetime.now(UTC) + timedelta(seconds=50)  # honours X-RateLimit-Reset

    third = await _tick(due + timedelta(seconds=1))
    assert third.succeeded == 1, third
    assert len(restarted.statuses) == 3  # one thread, no duplicate
    keys = [p["idempotency_key"] for p in restarted.posts]
    assert keys[0] == keys[1]  # the rate-limited part was re-sent with the same key


# ------------------------------------------------------------------ review regressions
async def test_settings_are_frozen_while_live_and_a_new_server_needs_a_new_token(
    live: Live,
) -> None:
    """Review MAJOR 1: a live account cannot be repointed (the token would follow), and a
    token only decrypts for the server it was stored for."""
    account = await _ready_account(live, sandbox=True)
    aid = account["id"]
    moved = {"instance_base_url": "https://elsewhere.example", "visibility": "unlisted"}
    refused = await _call(live, "PATCH", f"/platform-accounts/{aid}", json={"settings": moved})
    assert refused.status_code == 409 and refused.json()["details"]["field"] == "settings"
    off = await _call(live, "POST", f"/platform-accounts/{aid}/live", json={"enabled": False})
    assert off.status_code == 200
    ok = await _call(live, "PATCH", f"/platform-accounts/{aid}", json={"settings": moved})
    assert ok.status_code == 200, ok.text
    view = ok.json()
    assert view["has_credential"] is False and "NO_CREDENTIAL" in view["live_blockers"]
    creds = (await _call(live, "GET", f"/platform-accounts/{aid}/credentials")).json()["items"]
    assert creds and all(c["revoked_at"] is not None for c in creds)
    assert "instance address changed" in await _audit_dump()


async def test_a_mode_change_between_attempts_is_refused_and_auth_failures_stop(
    live: Live,
) -> None:
    """Review MAJOR 2: a retry must not be recorded with evidence it did not have; and a
    401 from the platform is a proven, non-retried failure."""
    asset = await _approved_thread(live)
    account = await _ready_account(live, sandbox=True)
    _mode(live, live_mode=True)
    job = await _publish_now(live, asset, account["id"])
    live.fake.plan.append("rate_limit")
    assert (await _tick()).retry_wait == 1
    got = await _job(live, job["id"])
    _mode(live, live_mode=False)  # the operator switches back to MOCK before the retry
    result = await _tick(datetime.fromisoformat(got["due_at"]) + timedelta(seconds=1))
    assert result.failed == 1, result
    got = await _job(live, job["id"])
    assert got["status"] == "FAILED" and got["last_error"]["guard"] == "MODE_CHANGED"
    pub = await _publication_of(live, job["id"])
    assert pub["status"] == "FAILED" and pub["evidence_class"] == "SANDBOX"
    assert len(live.fake.posts) == 1  # only the rate-limited attempt reached the server

    # A wrong token: 401 -> FAILED (PLATFORM_AUTH_FAILURE), never retried.
    assert (await _store_token(live, account["id"], token="fake_wrong_token_x")).status_code == 201
    _mode(live, live_mode=True)
    again = await _publish_now(live, asset, account["id"])
    assert (await _tick()).failed == 1
    failed = await _job(live, again["id"])
    assert failed["status"] == "FAILED" and failed["last_error_code"] == "PLATFORM_AUTH_FAILURE"
    assert (await _tick(datetime.now(UTC) + timedelta(hours=1))).claimed == 0
    assert live.fake.statuses == {}
