"""Integration: known issues resolved after the MVP milestone.

REGENERATE (§37, CA18/CA19) via approvals and /content/{id}/regenerate; EDIT via approvals
and of a SCHEDULED asset (CA17, job cancelled, approval invalidation recorded); QA re-run
endpoint; QA errors after generation reported instead of stranding assets; brand
auto-approve (CA7 by SYSTEM); recommendation accept / dismiss (RECOMMENDATION machine).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omnigrowth.core.enums import JobStatus, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.publishing.config import load_publishing_config
from omnigrowth.publishing.mock import MockPublisher
from omnigrowth.publishing.scheduler import run_once
from omnigrowth.services import content as content_service
from omnigrowth.workflows.tasks import execute_job
from tests.integration.campaign_support import (
    Env,
    _wizard_body,
    approve_all,
    fixture_deps,
    generated_campaign,
    platform_accounts,
)
from tests.integration.helpers import auth_headers

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def env(campaign_env: Env) -> Env:
    return campaign_env


async def _get(env: Env, path: str, **params: Any) -> Any:
    resp = await env.client.get(f"/api/v1{path}", headers=env.headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _action(env: Env, asset_id: str, **body: Any) -> Any:
    return await env.client.post(
        f"/api/v1/approvals/{asset_id}/actions", headers=env.headers, json=body
    )


async def _assets(env: Env, campaign_id: str, status: str) -> list[dict[str, Any]]:
    return list((await _get(env, "/content", campaign_id=campaign_id, status=status))["items"])


# ------------------------------------------------------------------ REGENERATE
async def test_regenerate_via_approvals_creates_a_regeneration_version(env: Env) -> None:
    campaign_id = await generated_campaign(env)
    failed = (await _assets(env, campaign_id, "QA_FAILED"))[0]
    v1 = failed["current_version"]
    resp = await _action(
        env,
        failed["id"],
        action="REGENERATE",
        content_version_id=v1["id"],
        comment="Keep it under 200 words and end with the readiness-call CTA.",
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["asset"]["status"] == "DRAFT"
    job = resp.json()["job"]
    assert job["kind"] == "ASSET_GENERATION" and resp.headers["X-Job-ID"] == job["id"]
    assert await execute_job(uuid.UUID(job["id"]), agent_deps=fixture_deps()) is JobStatus.SUCCEEDED

    after = await _get(env, f"/content/{failed['id']}")
    v2 = after["current_version"]
    assert v2["version_no"] == 2 and v2["origin"] == "REGENERATION"
    assert v2["based_on_version_id"] == v1["id"]
    assert v2["prompt_version_id"] and v2["model_run_id"]
    assert after["status"] in {"AWAITING_APPROVAL", "QA_FAILED"}
    history = (await _get(env, f"/approvals/{failed['id']}/history"))["items"]
    regen = [h for h in history if h["action"] == "REGENERATE"]
    assert regen and regen[0]["comment"].startswith("Keep it under")
    assert regen[0]["from_status"] == "QA_FAILED" and regen[0]["to_status"] == "DRAFT"


async def test_regenerate_endpoint_requires_the_current_version(env: Env) -> None:
    campaign_id = await generated_campaign(env)
    asset = (await _assets(env, campaign_id, "AWAITING_APPROVAL"))[0]
    stale = await env.client.post(
        f"/api/v1/content/{asset['id']}/regenerate",
        headers=env.headers,
        json={"content_version_id": str(uuid.uuid4())},
    )
    assert stale.status_code == 409
    ok = await env.client.post(
        f"/api/v1/content/{asset['id']}/regenerate",
        headers=env.headers,
        json={"content_version_id": asset["current_version_id"], "instructions": "shorter"},
    )
    assert ok.status_code == 202 and ok.json()["kind"] == "ASSET_GENERATION"
    # DRAFT cannot be regenerated again until the first request produced a version.
    again = await env.client.post(
        f"/api/v1/content/{asset['id']}/regenerate",
        headers=env.headers,
        json={"content_version_id": asset["current_version_id"]},
    )
    assert again.status_code == 409


# ------------------------------------------------------------------ EDIT (+ CA17)
async def test_edit_of_a_scheduled_asset_cancels_its_job_and_voids_approval(env: Env) -> None:
    campaign_id = await generated_campaign(env)
    approved = await approve_all(env, campaign_id)
    accounts = await platform_accounts(env, "LINKEDIN", "X")
    asset = next(a for a in approved if a["platform"] == "LINKEDIN")
    scheduled = await _action(
        env,
        asset["id"],
        action="SCHEDULE",
        content_version_id=asset["approved_version_id"],
        platform_account_id=accounts["LINKEDIN"],
        scheduled_at=(datetime.now(UTC) + timedelta(hours=4)).isoformat(),
    )
    job = scheduled.json()["publication_job"]
    base = await _get(env, f"/content/{asset['id']}/versions/{asset['approved_version_id']}")
    edit = await _action(
        env,
        asset["id"],
        action="EDIT",
        content_version_id=asset["approved_version_id"],
        comment="tighten the opening",
        edit={"body": base["body"], "hook": "Keep client data in the office, still use AI."},
    )
    assert edit.status_code == 200, edit.text
    result = edit.json()
    assert result["asset"]["status"] == "GENERATED"
    assert result["asset"]["approved_version_id"] is None
    assert result["version"]["origin"] == "HUMAN_EDIT" and result["job"]["kind"] == "QA_RUN"
    assert (await _get(env, f"/publications/jobs/{job['id']}"))["status"] == "CANCELLED"
    events = (await _get(env, f"/audit/timeline/content_assets/{asset['id']}"))["items"]
    edit_event = next(
        e
        for e in events
        if e["action"] == "status.transition" and e["metadata"]["trigger"] == "EDIT"
    )
    assert edit_event["metadata"]["from"] == "SCHEDULED"
    assert edit_event["metadata"]["invalidated_approval_of"] == asset["approved_version_id"]

    # QA re-run endpoint: allowed while GENERATED, refused once QA has moved it on.
    rerun = await env.client.post(f"/api/v1/content/{asset['id']}/qa", headers=env.headers)
    assert rerun.status_code == 202 and rerun.json()["kind"] == "QA_RUN"
    await execute_job(uuid.UUID(result["job"]["id"]), agent_deps=fixture_deps())
    assert (await _get(env, f"/content/{asset['id']}"))["status"] in {
        "AWAITING_APPROVAL",
        "QA_FAILED",
    }
    refused = await env.client.post(f"/api/v1/content/{asset['id']}/qa", headers=env.headers)
    assert refused.status_code == 409


# ------------------------------------------------------------------ QA errors after CA2
async def test_qa_error_after_generation_is_reported_not_stranded(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_qa(*args: Any, **kwargs: Any) -> str:
        raise OmniGrowthError(ErrorCode.DATABASE_FAILURE, "qa store unavailable")

    resp = await env.client.post(
        "/api/v1/campaigns/wizard",
        headers=env.headers,
        json=_wizard_body(env),
    )
    campaign_id = resp.json()["campaign"]["id"]
    await execute_job(uuid.UUID(resp.json()["job"]["id"]), agent_deps=fixture_deps())
    await env.client.post(f"/api/v1/campaigns/{campaign_id}/activate", headers=env.headers)
    gen = await env.client.post(
        f"/api/v1/campaigns/{campaign_id}/generate-assets", headers=env.headers
    )
    monkeypatch.setattr(content_service, "run_asset_qa", broken_qa)
    status = await execute_job(uuid.UUID(gen.json()["id"]), agent_deps=fixture_deps())
    assert status is JobStatus.SUCCEEDED
    result = (await _get(env, f"/jobs/{gen.json()['id']}"))["result"]
    assert len(result["qa_pending"]) == len(result["generated"]) == 8
    assert result["failed"] == []
    monkeypatch.undo()
    generated = await _assets(env, campaign_id, "GENERATED")
    assert len(generated) == 8  # content kept, waiting for QA — never silently skipped
    rerun = await env.client.post(f"/api/v1/content/{generated[0]['id']}/qa", headers=env.headers)
    assert rerun.status_code == 202
    await execute_job(uuid.UUID(rerun.json()["id"]), agent_deps=fixture_deps())
    assert (await _get(env, f"/content/{generated[0]['id']}"))["status"] != "GENERATED"


# ------------------------------------------------------------------ auto-approve
async def test_brand_auto_approve_approves_qa_passed_content_as_system(env: Env) -> None:
    owner = await auth_headers(env.client, UserRole.OWNER)
    toggled = await env.client.patch(
        f"/api/v1/brands/{env.brand_id}", headers=owner, json={"auto_approve_enabled": True}
    )
    assert toggled.status_code == 200, toggled.text
    campaign_id = await generated_campaign(env)
    assert await _assets(env, campaign_id, "AWAITING_APPROVAL") == []
    approved = await _assets(env, campaign_id, "APPROVED")
    assert len(approved) == 4  # the four that passed QA; the QA_FAILED ones stay put
    assert len(await _assets(env, campaign_id, "QA_FAILED")) == 4
    history = (await _get(env, f"/approvals/{approved[0]['id']}/history"))["items"]
    assert [(h["action"], h["actor_type"], h["actor_id"]) for h in history] == [
        ("APPROVE", "SYSTEM", "auto-approve")
    ]
    assert approved[0]["approved_version_id"] == approved[0]["current_version_id"]


# ------------------------------------------------------------------ recommendations
async def test_recommendations_are_accepted_or_dismissed_once_by_a_human(env: Env) -> None:
    campaign_id = await generated_campaign(env)
    approved = await approve_all(env, campaign_id)
    accounts = await platform_accounts(env, "LINKEDIN", "X")
    due = datetime.now(UTC) + timedelta(minutes=10)
    for asset in approved:
        resp = await _action(
            env,
            asset["id"],
            action="SCHEDULE",
            content_version_id=asset["approved_version_id"],
            platform_account_id=accounts[asset["platform"]],
            scheduled_at=due.isoformat(),
        )
        assert resp.status_code == 200, resp.text
    await run_once(
        publisher=MockPublisher(),
        config=load_publishing_config(REPO / "config"),
        now=due + timedelta(seconds=1),
    )
    pull = await env.client.post(
        "/api/v1/analytics/pull", headers=env.headers, json={"campaign_id": campaign_id}
    )
    await execute_job(uuid.UUID(pull.json()["id"]), agent_deps=fixture_deps())
    gen = await env.client.post(
        "/api/v1/recommendations/generate", headers=env.headers, json={"campaign_id": campaign_id}
    )
    assert await execute_job(uuid.UUID(gen.json()["id"]), agent_deps=fixture_deps()) is (
        JobStatus.SUCCEEDED
    )
    recs = (await _get(env, "/recommendations", campaign_id=campaign_id))["items"]
    assert len(recs) >= 2 and {r["status"] for r in recs} == {"PROPOSED"}

    viewer = await auth_headers(env.client, UserRole.VIEWER)
    denied = await env.client.post(
        f"/api/v1/recommendations/{recs[0]['id']}/accept", headers=viewer
    )
    assert denied.status_code == 403
    accepted = await env.client.post(
        f"/api/v1/recommendations/{recs[0]['id']}/accept", headers=env.headers
    )
    assert accepted.status_code == 200 and accepted.json()["status"] == "ACCEPTED"
    assert accepted.json()["decided_by_actor_type"] == "HUMAN"
    twice = await env.client.post(
        f"/api/v1/recommendations/{recs[0]['id']}/dismiss", headers=env.headers
    )
    assert twice.status_code == 409  # a decision is final
    dismissed = await env.client.post(
        f"/api/v1/recommendations/{recs[1]['id']}/dismiss", headers=env.headers
    )
    assert dismissed.json()["status"] == "DISMISSED"
    events = (await _get(env, f"/audit/timeline/recommendations/{recs[0]['id']}"))["items"]
    assert [(e["metadata"]["from"], e["metadata"]["to"]) for e in events] == [
        (None, "PROPOSED"),
        ("PROPOSED", "ACCEPTED"),
    ]
