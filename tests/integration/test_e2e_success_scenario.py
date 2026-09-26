"""§55 success scenario, end to end in mock mode (Phase 7 MVP gate; API_PLAN §4).

Brand -> platforms -> article -> campaign (research, strategy, ideas, mix, 7-day plan) ->
platform-native content + QA -> approval -> scheduler -> mock publication -> mock analytics
-> report -> evidence-grounded recommendations -> audit trail. All LLM calls replay
recorded fixtures (A8); YOUTUBE/INSTAGRAM items are reported as skipped (decision D2).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omnigrowth.analytics.metrics import DEFINITION_KEYS
from omnigrowth.core.enums import AngleType, JobStatus, UserRole
from omnigrowth.publishing.config import load_publishing_config
from omnigrowth.publishing.mock import MockPublisher
from omnigrowth.publishing.scheduler import run_once
from omnigrowth.workflows.tasks import execute_job
from tests.integration.campaign_support import ARTICLE, START, TZ, Env, fixture_deps
from tests.integration.helpers import auth_headers

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
TOPIC = "Local AI agents will become an important alternative to fully cloud-hosted AI."


@pytest.fixture
def env(campaign_env: Env) -> Env:
    return campaign_env


async def _ok(env: Env, method: str, path: str, expect: int = 200, **kwargs: Any) -> Any:
    resp = await env.client.request(method, f"/api/v1{path}", headers=env.headers, **kwargs)
    assert resp.status_code == expect, f"{method} {path}: {resp.status_code} {resp.text}"
    return resp.json()


async def _run(job_id: str) -> None:
    status = await execute_job(uuid.UUID(job_id), agent_deps=fixture_deps())
    assert status is JobStatus.SUCCEEDED, job_id


async def test_success_scenario_end_to_end(env: Env) -> None:
    # 1-2. Brand + audience (fixture) ----------------------------------------------------
    brand = await _ok(env, "GET", f"/brands/{env.brand_id}")
    assert brand["display_name"] == "Simply AI Solutions"
    audiences = await _ok(env, "GET", f"/brands/{env.brand_id}/audiences")
    audience_id = audiences["items"][0]["id"]

    # 4. Platforms configured (OWNER); never live -----------------------------------------
    owner = await auth_headers(env.client, UserRole.OWNER)
    accounts: dict[str, str] = {}
    for platform in ("LINKEDIN", "X", "YOUTUBE", "INSTAGRAM"):
        resp = await env.client.post(
            "/api/v1/platform-accounts",
            headers=owner,
            json={
                "brand_id": env.brand_id,
                "platform": platform,
                "account_type": "ORGANIZATION",
                "handle": f"simply-ai-{platform.lower()}",
                "timezone": TZ,
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["live_enabled"] is False
        assert resp.json()["effective_publisher"] == "MOCK"
        accounts[platform] = resp.json()["id"]

    # 5. Article -> 1. ingests source ------------------------------------------------------
    source = await _ok(
        env,
        "POST",
        "/sources/text",
        201,
        json={
            "brand_id": env.brand_id,
            "source_type": "TEXT",
            "title": "Why Local AI Agents Will Matter for Small Businesses",
            "content": ARTICLE,
        },
    )
    assert source["status"] == "NORMALIZED" and len(source["checksum"]) == 64

    # 6-7. Campaign wizard -> 2-7. ideas, research, strategy, opportunities, mix, plan -----
    wizard = await _ok(
        env,
        "POST",
        "/campaigns/wizard",
        202,
        json={
            "brand_id": env.brand_id,
            "name": "Local AI authority",
            "topic": TOPIC,
            "objective": "AUTHORITY",
            "audience_id": audience_id,
            "primary_platform": "LINKEDIN",
            "secondary_platforms": ["X", "YOUTUBE", "INSTAGRAM"],
            "source_asset_ids": [source["id"]],
            "start_date": START.isoformat(),
            "timezone": TZ,
            "generate": True,
        },
    )
    campaign_id = wizard["campaign"]["id"]
    assert wizard["campaign"]["status"] == "PLANNING"
    await _run(wizard["job"]["id"])
    assert (await _ok(env, "GET", f"/campaigns/{campaign_id}"))["status"] == "READY_FOR_REVIEW"

    # 8. Review reads -----------------------------------------------------------------------
    packet = await _ok(env, "GET", f"/campaigns/{campaign_id}/research-packet")
    assert packet["claims"] and all(c["label"] for c in packet["claims"])
    assert all(c["citation_url"] is None for c in packet["claims"])  # A5: no fabricated cites
    ideas = (await _ok(env, "GET", f"/campaigns/{campaign_id}/ideas"))["items"]
    assert len(ideas) >= 20 and {i["angle"] for i in ideas} == {a.value for a in AngleType}
    plan = (await _ok(env, "GET", f"/campaigns/{campaign_id}/plan"))["items"]
    assert {p["platform"] for p in plan} == {"LINKEDIN", "X", "YOUTUBE", "INSTAGRAM"}
    days = {datetime.fromisoformat(p["window_start"]).date() for p in plan}
    assert len(days) == 7

    # 9. Strategy accepted -------------------------------------------------------------------
    assert (await _ok(env, "POST", f"/campaigns/{campaign_id}/activate"))["status"] == "ACTIVE"

    # 10. 8. platform-native content, 9. QA, 10. to approval ----------------------------------
    gen = await _ok(env, "POST", f"/campaigns/{campaign_id}/generate-assets", 202)
    await _run(gen["id"])
    result = (await _ok(env, "GET", f"/jobs/{gen['id']}"))["result"]
    skipped = {s["platform"] for s in result["skipped"]}
    assert skipped == {"YOUTUBE", "INSTAGRAM"}  # D2: no MVP writer, reported not faked
    assert all(s["reason"] == "NOT_IMPLEMENTED" for s in result["skipped"])

    # 11. Inspect the approval queue ------------------------------------------------------------
    queue = (await _ok(env, "GET", "/approvals", params={"campaign_id": campaign_id}))["items"]
    assert queue, "nothing reached approval"
    for asset in queue:
        stages = {r["stage"]: r["outcome"] for r in asset["qa"]}
        assert set(stages) == {"STRUCTURAL", "BRAND", "ACCURACY", "PLATFORM"}
        assert "FAIL" not in stages.values()

    # 12. 11. user approves --------------------------------------------------------------------
    plan_by_id = {p["id"]: p for p in plan}
    approved = []
    for asset in queue:
        resp = await _ok(
            env,
            "POST",
            f"/approvals/{asset['id']}/actions",
            json={"action": "APPROVE", "content_version_id": asset["current_version_id"]},
        )
        assert resp["asset"]["status"] == "APPROVED"
        approved.append(resp["asset"])

    # 13. 12. scheduler queues posts (at each plan window) ---------------------------------------
    latest_due: datetime | None = None
    for asset in approved:
        window = datetime.fromisoformat(plan_by_id[asset["plan_item_id"]]["window_start"])
        due = max(window, datetime.now(window.tzinfo) + timedelta(minutes=5))
        resp = await _ok(
            env,
            "POST",
            f"/approvals/{asset['id']}/actions",
            json={
                "action": "SCHEDULE",
                "content_version_id": asset["approved_version_id"],
                "platform_account_id": accounts[asset["platform"]],
                "scheduled_at": due.isoformat(),
            },
        )
        assert resp["publication_job"]["status"] == "PENDING"
        assert resp["asset"]["status"] == "SCHEDULED"
        latest_due = due if latest_due is None or due > latest_due else latest_due
    assert latest_due is not None
    # Negative checks (API_PLAN §4): replaying a SCHEDULE with the same Idempotency-Key
    # returns the stored response and creates nothing; a new key reschedules (CA12), which
    # cancels the pending job instead of creating a second post (A3).
    body = {
        "action": "SCHEDULE",
        "content_version_id": approved[0]["approved_version_id"],
        "platform_account_id": accounts[approved[0]["platform"]],
        "scheduled_at": latest_due.isoformat(),
    }
    keyed = {**env.headers, "Idempotency-Key": "e2e-reschedule-0001"}
    url = f"/api/v1/approvals/{approved[0]['id']}/actions"
    moved = await env.client.post(url, headers=keyed, json=body)
    assert moved.status_code == 200, moved.text
    replay = await env.client.post(url, headers=keyed, json=body)
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["publication_job"]["id"] == moved.json()["publication_job"]["id"]

    # 14. 13. mock publisher publishes (frozen clock, one scheduler tick in-process) ---------------
    config = load_publishing_config(REPO / "config")
    await run_once(publisher=MockPublisher(), config=config, now=latest_due + timedelta(seconds=1))
    jobs = (await _ok(env, "GET", "/publications/jobs", params={"campaign_id": campaign_id}))[
        "items"
    ]
    live_jobs = [j for j in jobs if j["status"] != "CANCELLED"]
    assert [j["status"] for j in jobs].count("CANCELLED") == 1  # the rescheduled one
    assert len(live_jobs) == len(approved) and {j["status"] for j in live_jobs} == {"SUCCEEDED"}
    pubs = (await _ok(env, "GET", "/publications", params={"campaign_id": campaign_id}))["items"]
    assert len(pubs) == len(approved) and {p["status"] for p in pubs} == {"PUBLISHED"}
    assert all(p["external_post_id"].startswith("mock_") for p in pubs)
    for asset in approved:
        assert (await _ok(env, "GET", f"/content/{asset['id']}"))["status"] == "PUBLISHED"

    # 15. 14. mock analytics return data
    pull = await _ok(env, "POST", "/analytics/pull", 202, json={"campaign_id": campaign_id})
    await _run(pull["id"])
    snapshots = (
        await _ok(env, "GET", "/analytics/snapshots", params={"campaign_id": campaign_id})
    )["items"]
    assert len(snapshots) == len(pubs)
    x_snapshots = [
        s
        for s in snapshots
        if next(p for p in pubs if p["id"] == s["publication_id"])["platform"] == "X"
    ]
    assert x_snapshots and all(s["profile_visits"] is None for s in x_snapshots)  # not 0

    # 16. 15. analytics engine analyses -----------------------------------------------------------
    report = await _ok(env, "GET", f"/analytics/campaigns/{campaign_id}/report")
    assert report["coverage"] == {"published": len(pubs), "with_snapshot": len(pubs)}
    assert set(report["derived"]) == set(DEFINITION_KEYS)
    assert report["derived"]["engagement_rate"]["value"] > 0
    x_row = next(r for r in report["publications"] if r["platform"] == "X")
    follow = x_row["derived"]["follow_conversion"]
    assert follow["value"] is None and follow["null_reason"] == "profile_visits not captured"

    # 17. 16. evidence-grounded recommendations ---------------------------------------------------
    gen_recs = await _ok(
        env, "POST", "/recommendations/generate", 202, json={"campaign_id": campaign_id}
    )
    await _run(gen_recs["id"])
    rec_job = (await _ok(env, "GET", f"/jobs/{gen_recs['id']}"))["result"]
    assert len(rec_job["dropped"]) == 1  # the recommendation citing a missing row
    recs = (await _ok(env, "GET", "/recommendations", params={"campaign_id": campaign_id}))["items"]
    assert len(recs) == 2
    snapshot_ids = {s["id"] for s in snapshots}
    for rec in recs:
        assert rec["evidence_snapshot_ids"] and rec["status"] == "PROPOSED"
        for sid in rec["evidence_snapshot_ids"]:
            assert sid in snapshot_ids
            assert (await _ok(env, "GET", f"/analytics/snapshots/{sid}"))["id"] == sid

    # 18. Auditability -----------------------------------------------------------------------------
    asset_id = approved[0]["id"]
    events = (await _ok(env, "GET", f"/audit/timeline/content_assets/{asset_id}"))["items"]
    moves = [
        (e["metadata"]["from"], e["metadata"]["to"])
        for e in events
        if e["action"] == "status.transition"
    ]
    assert moves == [
        (None, "DRAFT"),
        ("DRAFT", "GENERATED"),
        ("GENERATED", "QA_PASSED"),
        ("QA_PASSED", "AWAITING_APPROVAL"),
        ("AWAITING_APPROVAL", "APPROVED"),
        ("APPROVED", "SCHEDULED"),
        ("SCHEDULED", "SCHEDULED"),  # the reschedule above (CA12)
        ("SCHEDULED", "PUBLISHED"),
    ]
    assert all(e["actor_type"] and e["correlation_id"] for e in events)
    actors = [e["actor_type"] for e in events if e["action"] == "status.transition"]
    assert actors == ["HUMAN", "AGENT", "SYSTEM", "SYSTEM", "HUMAN", "HUMAN", "HUMAN", "SYSTEM"]
