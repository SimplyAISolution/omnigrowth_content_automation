"""Integration: Phase 7 gate — approvals (§37), outbox scheduler (A3), mock publisher (§34).

Covers: schedule -> tick -> PUBLISHED; duplicate schedule 409; two schedulers racing ->
exactly one publication; PUBLICATION_UNCERTAIN (block, hold, no auto-retry, owner-only
confirmation); retry with backoff; lease recovery J10/J11; live guard in ENV=test (A6).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omnigrowth.core.enums import Environment, PublishMode, UserRole
from omnigrowth.core.settings import get_settings
from omnigrowth.publishing.base import FailureKind
from omnigrowth.publishing.config import load_publishing_config
from omnigrowth.publishing.mock import MockPublisher
from omnigrowth.publishing.scheduler import TickResult, claim_due, recover_expired_leases, run_once
from tests.integration.campaign_support import (
    Env,
    approve_all,
    generated_campaign,
    platform_accounts,
)
from tests.integration.helpers import auth_headers

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
CONFIG = load_publishing_config(REPO / "config")


@pytest.fixture
def env(campaign_env: Env) -> Env:
    return campaign_env


class Ready:
    campaign_id: str
    assets: list[dict[str, Any]]
    accounts: dict[str, str]


async def _ready(env: Env) -> Ready:
    r = Ready()
    r.campaign_id = await generated_campaign(env)
    r.assets = await approve_all(env, r.campaign_id)
    r.accounts = await platform_accounts(env, "LINKEDIN", "X")
    assert {a["status"] for a in r.assets} == {"APPROVED"}
    return r


def _of(ready: Ready, platform: str) -> list[dict[str, Any]]:
    return [a for a in ready.assets if a["platform"] == platform]


async def _schedule(
    env: Env, ready: Ready, asset: dict[str, Any], when: datetime, action: str = "SCHEDULE"
) -> Any:
    body: dict[str, Any] = {
        "action": action,
        "content_version_id": asset["approved_version_id"],
        "platform_account_id": ready.accounts[asset["platform"]],
    }
    if action == "SCHEDULE":
        body["scheduled_at"] = when.isoformat()
    return await env.client.post(
        f"/api/v1/approvals/{asset['id']}/actions", headers=env.headers, json=body
    )


async def _get(env: Env, path: str, **params: Any) -> Any:
    resp = await env.client.get(f"/api/v1{path}", headers=env.headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _versions(ready: Ready) -> set[str]:
    return {str(a["approved_version_id"]) for a in ready.assets}


def _mine(publisher: MockPublisher, ready: Ready) -> int:
    """Platform calls for this test's versions (the scheduler is global: other tests' due
    jobs in the shared database may be published by the same tick)."""
    versions = {a["approved_version_id"] for a in ready.assets}
    return sum(str(r.content_version_id) in versions for r in publisher.requests)


async def _tick(publisher: MockPublisher, now: datetime, scheduler_id: str = "s1") -> TickResult:
    return await run_once(publisher=publisher, config=CONFIG, now=now, scheduler_id=scheduler_id)


# ------------------------------------------------------------------ happy path
async def test_scheduled_asset_is_published_by_the_scheduler(env: Env) -> None:
    ready = await _ready(env)
    asset = _of(ready, "LINKEDIN")[0]
    due = datetime.now(UTC) + timedelta(hours=2)
    resp = await _schedule(env, ready, asset, due)
    assert resp.status_code == 200, resp.text
    job = resp.json()["publication_job"]
    assert job["status"] == "PENDING" and resp.json()["asset"]["status"] == "SCHEDULED"
    assert len(job["idempotency_key"]) == 64

    publisher = MockPublisher()
    await _tick(publisher, due - timedelta(minutes=1))
    assert _mine(publisher, ready) == 0  # not due yet
    result = await _tick(publisher, due + timedelta(seconds=1))
    assert job["id"] in result.job_ids

    done = await _get(env, f"/publications/jobs/{job['id']}")
    assert done["status"] == "SUCCEEDED" and done["attempt_count"] == 1
    pubs = (await _get(env, "/publications", campaign_id=ready.campaign_id))["items"]
    assert len(pubs) == 1 and pubs[0]["status"] == "PUBLISHED"
    assert pubs[0]["external_post_id"].startswith("mock_") and pubs[0]["publish_mode"] == "MOCK"
    assert (await _get(env, f"/content/{asset['id']}"))["status"] == "PUBLISHED"

    history = (await _get(env, f"/approvals/{asset['id']}/history"))["items"]
    assert [(h["action"], h["actor_type"]) for h in history] == [
        ("APPROVE", "HUMAN"),
        ("SCHEDULE", "HUMAN"),
    ]
    assert history[1]["platform_account_id"] == ready.accounts["LINKEDIN"]

    again = await _tick(publisher, due + timedelta(minutes=5))
    assert job["id"] not in again.job_ids and _mine(publisher, ready) == 1  # exactly once


async def test_duplicate_schedule_is_a_conflict(env: Env) -> None:
    ready = await _ready(env)
    asset = _of(ready, "X")[0]
    due = datetime.now(UTC) + timedelta(hours=1)
    assert (await _schedule(env, ready, asset, due)).status_code == 200
    again = await _schedule(env, ready, asset, due + timedelta(minutes=5))
    assert again.status_code == 409  # asset already SCHEDULED
    # Cancel (J9 + CA13), then the same version may be scheduled again as a new row.
    jobs = (await _get(env, "/publications/jobs", campaign_id=ready.campaign_id))["items"]
    cancel = await env.client.post(
        f"/api/v1/publications/jobs/{jobs[0]['id']}/cancel",
        headers=env.headers,
        json={"reason": "moving it"},
    )
    assert cancel.status_code == 200 and cancel.json()["status"] == "CANCELLED"
    assert (await _get(env, f"/content/{asset['id']}"))["status"] == "APPROVED"
    assert (await _schedule(env, ready, asset, due + timedelta(hours=1))).status_code == 200


async def test_schedule_requires_the_approved_version_and_matching_account(env: Env) -> None:
    ready = await _ready(env)
    asset = _of(ready, "LINKEDIN")[0]
    wrong_platform = await env.client.post(
        f"/api/v1/approvals/{asset['id']}/actions",
        headers=env.headers,
        json={
            "action": "PUBLISH_NOW",
            "content_version_id": asset["approved_version_id"],
            "platform_account_id": ready.accounts["X"],
        },
    )
    assert wrong_platform.status_code == 422
    stale = await env.client.post(
        f"/api/v1/approvals/{asset['id']}/actions",
        headers=env.headers,
        json={
            "action": "PUBLISH_NOW",
            "content_version_id": str(uuid.uuid4()),
            "platform_account_id": ready.accounts["LINKEDIN"],
        },
    )
    assert stale.status_code == 409


# ------------------------------------------------------------------ gate: racing schedulers
async def test_two_schedulers_racing_publish_each_job_exactly_once(env: Env) -> None:
    ready = await _ready(env)
    due = datetime.now(UTC) + timedelta(minutes=30)
    for asset in ready.assets:
        assert (await _schedule(env, ready, asset, due)).status_code == 200
    publisher = MockPublisher()
    now = due + timedelta(seconds=1)
    a, b, c = await asyncio.gather(
        _tick(publisher, now, "scheduler-a"),
        _tick(publisher, now, "scheduler-b"),
        _tick(publisher, now, "scheduler-c"),
    )
    ids = a.job_ids + b.job_ids + c.job_ids
    assert len(ids) == len(set(ids))  # no job was claimed by two schedulers
    assert _mine(publisher, ready) == len(ready.assets)  # one platform call per job, never two
    pubs = (await _get(env, "/publications", campaign_id=ready.campaign_id))["items"]
    assert len(pubs) == len(ready.assets)
    assert {p["status"] for p in pubs} == {"PUBLISHED"}
    assert len({p["content_asset_id"] for p in pubs}) == len(ready.assets)


# ------------------------------------------------------------------ gate: uncertain path
async def test_publication_uncertain_blocks_the_account_until_a_human_reconciles(
    env: Env,
) -> None:
    ready = await _ready(env)
    first, second = _of(ready, "LINKEDIN")[:2]
    due = datetime.now(UTC) + timedelta(minutes=10)
    job1 = (await _schedule(env, ready, first, due)).json()["publication_job"]
    job2 = (await _schedule(env, ready, second, due + timedelta(minutes=1))).json()[
        "publication_job"
    ]
    publisher = MockPublisher(faults=[FailureKind.AFTER_SEND], fault_versions=_versions(ready))

    await _tick(publisher, due + timedelta(seconds=1))
    assert _mine(publisher, ready) == 1
    j1 = await _get(env, f"/publications/jobs/{job1['id']}")
    assert j1["status"] == "UNCERTAIN" and j1["last_error_code"] == "PUBLICATION_UNCERTAIN"
    pub = (await _get(env, "/publications", campaign_id=ready.campaign_id))["items"][0]
    assert pub["status"] == "PUBLICATION_UNCERTAIN"
    assert (await _get(env, f"/content/{first['id']}"))["status"] == "SCHEDULED"
    accounts = (await _get(env, "/platform-accounts", brand_id=env.brand_id))["items"]
    assert {a["platform"]: a["has_open_uncertain"] for a in accounts}["LINKEDIN"] is True

    # Guard 4: the other job on the same account is held, attempt not counted.
    r2 = await _tick(publisher, due + timedelta(minutes=2))
    assert job2["id"] in r2.job_ids and _mine(publisher, ready) == 1
    j2 = await _get(env, f"/publications/jobs/{job2['id']}")
    assert j2["status"] == "RETRY_WAIT" and j2["attempt_count"] == 0
    assert j2["last_error"] == {"guard": "OPEN_UNCERTAIN"}

    # UNCERTAIN is never retried automatically, however many ticks pass.
    for minutes in (30, 60, 120):
        await _tick(publisher, due + timedelta(minutes=minutes))
    assert (await _get(env, f"/publications/jobs/{job1['id']}"))["status"] == "UNCERTAIN"
    assert _mine(publisher, ready) == 1

    body = {"method": "MANUAL_CONFIRM_NOT_PUBLISHED", "note": "checked the page, not there"}
    denied = await env.client.post(
        f"/api/v1/publications/{pub['id']}/reconcile", headers=env.headers, json=body
    )
    assert denied.status_code == 403  # EDITOR may not declare "not published" (J13)
    owner = await auth_headers(env.client, UserRole.OWNER)
    ok = await env.client.post(
        f"/api/v1/publications/{pub['id']}/reconcile", headers=owner, json=body
    )
    assert ok.status_code == 200 and ok.json()["status"] == "FAILED"
    assert (await _get(env, f"/publications/jobs/{job1['id']}"))["status"] == "FAILED"
    assert (await _get(env, f"/content/{first['id']}"))["status"] == "APPROVED"

    # Account released: the held job (re-held by every tick above, last at +120 min) is
    # published once its hold expires.
    held = await _get(env, f"/publications/jobs/{job2['id']}")
    assert held["status"] == "RETRY_WAIT" and held["attempt_count"] == 0
    released = await _tick(publisher, datetime.fromisoformat(held["due_at"]) + timedelta(seconds=1))
    assert job2["id"] in released.job_ids
    assert (await _get(env, f"/publications/jobs/{job2['id']}"))["status"] == "SUCCEEDED"


async def test_uncertain_publication_confirmed_published_by_an_editor(env: Env) -> None:
    ready = await _ready(env)
    asset = _of(ready, "X")[0]
    due = datetime.now(UTC) + timedelta(minutes=10)
    job = (await _schedule(env, ready, asset, due)).json()["publication_job"]
    broken = MockPublisher(faults=[FailureKind.AFTER_SEND], fault_versions=_versions(ready))
    await _tick(broken, due + timedelta(seconds=1))
    pub = (await _get(env, "/publications", campaign_id=ready.campaign_id))["items"][0]
    bad = await env.client.post(
        f"/api/v1/publications/{pub['id']}/reconcile",
        headers=env.headers,
        json={"method": "MANUAL_CONFIRM_PUBLISHED", "canonical_url": "http://x.com/p/1"},
    )
    assert bad.status_code == 422
    ok = await env.client.post(
        f"/api/v1/publications/{pub['id']}/reconcile",
        headers=env.headers,
        json={
            "method": "MANUAL_CONFIRM_PUBLISHED",
            "canonical_url": "https://x.com/simplyai/status/123",
            "external_post_id": "123",
        },
    )
    assert ok.status_code == 200 and ok.json()["status"] == "PUBLISHED"
    assert ok.json()["canonical_url"] == "https://x.com/simplyai/status/123"
    assert (await _get(env, f"/publications/jobs/{job['id']}"))["status"] == "SUCCEEDED"
    assert (await _get(env, f"/content/{asset['id']}"))["status"] == "PUBLISHED"


# ------------------------------------------------------------------ retries and leases
async def test_rate_limit_is_retried_with_backoff(env: Env) -> None:
    ready = await _ready(env)
    asset = _of(ready, "LINKEDIN")[0]
    due = datetime.now(UTC) + timedelta(minutes=10)
    job = (await _schedule(env, ready, asset, due)).json()["publication_job"]
    publisher = MockPublisher(faults=[FailureKind.RATE_LIMIT], fault_versions=_versions(ready))
    await _tick(publisher, due + timedelta(seconds=1))
    waiting = await _get(env, f"/publications/jobs/{job['id']}")
    assert waiting["status"] == "RETRY_WAIT" and waiting["attempt_count"] == 1
    assert waiting["last_error_code"] == "PLATFORM_RATE_LIMIT"
    assert (await _get(env, f"/content/{asset['id']}"))["status"] == "SCHEDULED"
    retry_at = datetime.fromisoformat(waiting["due_at"])
    assert due < retry_at <= due + timedelta(seconds=1 + CONFIG.publishing.backoff_base_seconds)
    await _tick(publisher, retry_at + timedelta(seconds=1))
    done = await _get(env, f"/publications/jobs/{job['id']}")
    assert done["status"] == "SUCCEEDED" and done["attempt_count"] == 2


async def test_auth_failure_is_final_and_returns_the_asset_to_approved(env: Env) -> None:
    ready = await _ready(env)
    asset = _of(ready, "LINKEDIN")[0]
    due = datetime.now(UTC) + timedelta(minutes=10)
    job = (await _schedule(env, ready, asset, due)).json()["publication_job"]
    broken = MockPublisher(faults=[FailureKind.AUTH], fault_versions=_versions(ready))
    await _tick(broken, due + timedelta(seconds=1))
    failed = await _get(env, f"/publications/jobs/{job['id']}")
    assert failed["status"] == "FAILED" and failed["last_error_code"] == "PLATFORM_AUTH_FAILURE"
    pub = (await _get(env, "/publications", campaign_id=ready.campaign_id))["items"][0]
    assert pub["status"] == "FAILED"
    assert (await _get(env, f"/content/{asset['id']}"))["status"] == "APPROVED"


async def test_expired_leases_recover_to_pending_or_uncertain(env: Env) -> None:
    ready = await _ready(env)
    li = _of(ready, "LINKEDIN")
    due = datetime.now(UTC) + timedelta(minutes=10)
    job_a = (await _schedule(env, ready, li[0], due)).json()["publication_job"]
    now = due + timedelta(seconds=1)
    # A scheduler claims and then dies before dispatching anything.
    claimed = await claim_due(now, scheduler_id="dies", config=CONFIG)
    assert [str(j) for j, _ in claimed] == [job_a["id"]]
    assert (await _get(env, f"/publications/jobs/{job_a['id']}"))["status"] == "CLAIMED"
    result = TickResult()
    later = now + timedelta(seconds=CONFIG.scheduler.lease_seconds + 1)
    await recover_expired_leases(later, publisher=MockPublisher(), scheduler_id="s2", result=result)
    back = await _get(env, f"/publications/jobs/{job_a['id']}")
    assert back["status"] == "PENDING" and back["dispatch_started_at"] is None  # J10

    # Same crash, but after dispatch had started (the post may exist): J11 -> UNCERTAIN.
    from omnigrowth.publishing.scheduler import _start

    publisher = MockPublisher()
    claimed = await claim_due(later, scheduler_id="dies-later", config=CONFIG)
    from omnigrowth.storage.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        request = await _start(
            session,
            claimed[0][0],
            claimed[0][1],
            later,
            publisher=publisher,
            config=CONFIG,
            actor=__import__("omnigrowth.core.state_machines", fromlist=["Actor"]).Actor(
                __import__("omnigrowth.core.enums", fromlist=["ActorType"]).ActorType.SCHEDULER,
                "dies-later",
            ),
            result=TickResult(),
        )
    assert request is not None
    much_later = later + timedelta(seconds=CONFIG.scheduler.lease_seconds + 1)
    await recover_expired_leases(
        much_later, publisher=publisher, scheduler_id="s3", result=TickResult()
    )
    stuck = await _get(env, f"/publications/jobs/{job_a['id']}")
    assert stuck["status"] == "UNCERTAIN" and stuck["attempt_count"] == 1
    pub = (await _get(env, "/publications", campaign_id=ready.campaign_id))["items"][0]
    assert pub["status"] == "PUBLICATION_UNCERTAIN"


# ------------------------------------------------------------------ A6 live guard
async def test_live_dispatch_is_impossible_in_the_test_environment(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = await _ready(env)
    asset = _of(ready, "LINKEDIN")[0]
    due = datetime.now(UTC) + timedelta(minutes=10)
    job = (await _schedule(env, ready, asset, due)).json()["publication_job"]
    # Settings validation already refuses LIVE with ENV=test; force it in-process to prove
    # the dispatcher's own independent re-check (A6 part b).
    forced = get_settings().model_copy(
        update={"publish_mode": PublishMode.LIVE, "env": Environment.TEST}
    )
    monkeypatch.setattr("omnigrowth.publishing.scheduler.get_settings", lambda: forced)
    publisher = MockPublisher()
    result = await _tick(publisher, due + timedelta(seconds=1))
    assert job["id"] in result.job_ids and _mine(publisher, ready) == 0  # nothing sent
    blocked = await _get(env, f"/publications/jobs/{job['id']}")
    assert blocked["status"] == "FAILED" and blocked["last_error_code"] == "LIVE_PUBLISH_BLOCKED"
    assert blocked["last_error"] == {"guard": "ENV_TEST"}
    assert (await _get(env, f"/content/{asset['id']}"))["status"] == "APPROVED"


# ------------------------------------------------------------------ review regressions
async def test_claim_skips_rows_locked_by_another_scheduler(env: Env) -> None:
    """SKIP LOCKED, forced: a row locked elsewhere is skipped immediately, never waited on."""
    from sqlalchemy import select

    from omnigrowth.models import PublicationJob
    from omnigrowth.storage.db import get_sessionmaker

    ready = await _ready(env)
    due = datetime.now(UTC) + timedelta(minutes=10)
    job = (await _schedule(env, ready, _of(ready, "X")[0], due)).json()["publication_job"]
    now = due + timedelta(seconds=1)
    async with get_sessionmaker()() as holder:
        await holder.execute(
            select(PublicationJob.id)
            .where(PublicationJob.id == uuid.UUID(job["id"]))
            .with_for_update()
        )
        claimed = await asyncio.wait_for(
            claim_due(now, scheduler_id="other", config=CONFIG), timeout=10
        )
        assert uuid.UUID(job["id"]) not in {j for j, _ in claimed}
        await holder.rollback()
    claimed = await claim_due(now, scheduler_id="later", config=CONFIG)
    assert uuid.UUID(job["id"]) in {j for j, _ in claimed}


async def test_dispatch_refuses_when_the_database_says_the_lease_is_gone(env: Env) -> None:
    from omnigrowth.publishing.scheduler import dispatch_one

    ready = await _ready(env)
    asset = _of(ready, "LINKEDIN")[0]
    due = datetime.now(UTC) - timedelta(seconds=50)  # within the schedule grace
    job = (await _schedule(env, ready, asset, due)).json()["publication_job"]
    short = CONFIG.model_copy(
        update={
            "scheduler": CONFIG.scheduler.model_copy(
                update={"lease_seconds": 10, "request_timeout_seconds": 1}
            )
        }
    )
    claimed = await claim_due(due, scheduler_id="slow", config=short)
    token = dict(claimed)[uuid.UUID(job["id"])]
    publisher = MockPublisher()
    # The tick's own clock (``due``) thinks 9 s of lease remain; the database knows the
    # lease ended ~40 s ago, so dispatch start is refused and nothing is sent.
    await dispatch_one(
        uuid.UUID(job["id"]),
        token,
        due,
        publisher=publisher,
        config=short,
        scheduler_id="slow",
        result=TickResult(),
    )
    assert _mine(publisher, ready) == 0
    after = await _get(env, f"/publications/jobs/{job['id']}")
    assert after["status"] == "CLAIMED" and after["dispatch_started_at"] is None


class _RecoveredMidCall(MockPublisher):
    """Simulates another scheduler recovering the lease (J11) while the call is in flight."""

    def __init__(self, *, fail: bool, version: str) -> None:
        super().__init__()
        self._fail = fail
        self._version = version

    async def publish(self, request: Any) -> Any:
        result = await super().publish(request)
        if str(request.content_version_id) != self._version:
            return result  # another test's leftover job in the shared database
        far = datetime.now(UTC) + timedelta(days=30)
        await recover_expired_leases(far, publisher=self, scheduler_id="other", result=TickResult())
        if self._fail:
            from omnigrowth.publishing.base import PublishError

            raise PublishError(FailureKind.AFTER_SEND, "response lost")
        return result


async def test_late_success_after_lease_recovery_is_reconciled(env: Env) -> None:
    ready = await _ready(env)
    asset = _of(ready, "LINKEDIN")[0]
    due = datetime.now(UTC) + timedelta(minutes=10)
    job = (await _schedule(env, ready, asset, due)).json()["publication_job"]
    await _tick(
        _RecoveredMidCall(fail=False, version=asset["approved_version_id"]),
        due + timedelta(seconds=1),
    )
    done = await _get(env, f"/publications/jobs/{job['id']}")
    assert done["status"] == "SUCCEEDED"  # UNCERTAIN (J11) -> SUCCEEDED (J12) with evidence
    pub = (await _get(env, "/publications", campaign_id=ready.campaign_id))["items"][0]
    assert pub["status"] == "PUBLISHED" and pub["external_post_id"].startswith("mock_")
    assert pub["reconciled_at"] is not None
    assert (await _get(env, f"/content/{asset['id']}"))["status"] == "PUBLISHED"
    moves = [
        (e["metadata"]["from"], e["metadata"]["to"])
        for e in (await _get(env, f"/audit/timeline/publication_jobs/{job['id']}"))["items"]
        if e["action"] == "status.transition"
    ]
    assert ("CLAIMED", "UNCERTAIN") in moves and ("UNCERTAIN", "SUCCEEDED") in moves


async def test_late_failure_after_lease_recovery_is_audited_not_applied(env: Env) -> None:
    ready = await _ready(env)
    asset = _of(ready, "X")[0]
    due = datetime.now(UTC) + timedelta(minutes=10)
    job = (await _schedule(env, ready, asset, due)).json()["publication_job"]
    await _tick(
        _RecoveredMidCall(fail=True, version=asset["approved_version_id"]),
        due + timedelta(seconds=1),
    )
    stuck = await _get(env, f"/publications/jobs/{job['id']}")
    assert stuck["status"] == "UNCERTAIN"  # still needs a human; never retried
    events = (await _get(env, f"/audit/timeline/publication_jobs/{job['id']}"))["items"]
    late = [e for e in events if e["action"] == "publication.late_result"]
    assert late and late[0]["metadata"]["outcome"] == "failure"
