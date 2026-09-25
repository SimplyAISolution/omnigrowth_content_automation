"""Integration: Phase 6 gate — writers (LinkedIn, X), content versions + diff (§36), QA stages
1-4 (§11) and pgvector duplicate detection (§10), driven by recorded fixtures (A8).

Canonical writer answers live in scripts/author_canonical_llm.py; re-record with
``OMNIGROWTH_RECORD_FIXTURES=1`` (see tests/integration/test_campaign_planning.py).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from omnigrowth.agents.base import AgentDeps
from omnigrowth.core.enums import JobStatus, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.models import ApprovalAction, Embedding
from omnigrowth.prompts import PromptRegistry
from omnigrowth.providers.base import ChatRequest, ChatResult
from omnigrowth.providers.fake import FakeProvider
from omnigrowth.storage.db import get_sessionmaker
from omnigrowth.workflows.tasks import execute_job
from tests.integration.campaign_support import (
    FIXTURES,
    Env,
    _get,
    _router,
    _wizard_body,
    fixture_deps,
)
from tests.integration.helpers import auth_headers

pytestmark = pytest.mark.integration


@pytest.fixture
def env(campaign_env: Env) -> Env:
    return campaign_env


async def _active_campaign(env: Env) -> str:
    resp = await env.client.post(
        "/api/v1/campaigns/wizard", headers=env.headers, json=_wizard_body(env)
    )
    assert resp.status_code == 202, resp.text
    campaign_id = resp.json()["campaign"]["id"]
    job = uuid.UUID(resp.json()["job"]["id"])
    assert await execute_job(job, agent_deps=fixture_deps()) is JobStatus.SUCCEEDED
    activated = await env.client.post(
        f"/api/v1/campaigns/{campaign_id}/activate", headers=env.headers
    )
    assert activated.status_code == 200, activated.text
    return str(campaign_id)


async def _content(env: Env, path: str = "", **params: Any) -> Any:
    resp = await env.client.get(f"/api/v1/content{path}", headers=env.headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _codes(asset: dict[str, Any]) -> set[str]:
    return {f["code"] for r in asset["qa"] for f in r["findings"]}


async def _generated(env: Env) -> tuple[str, dict[tuple[int, str], dict[str, Any]]]:
    campaign_id = await _active_campaign(env)
    resp = await env.client.post(
        f"/api/v1/campaigns/{campaign_id}/generate-assets", headers=env.headers
    )
    assert resp.status_code == 202, resp.text
    job_id = uuid.UUID(resp.json()["id"])
    assert env.queue.enqueued[-1] == ("generate", job_id)
    assert await execute_job(job_id, agent_deps=fixture_deps()) is JobStatus.SUCCEEDED
    job = (await env.client.get(f"/api/v1/jobs/{job_id}", headers=env.headers)).json()
    assert len(job["result"]["generated"]) == 8, job["result"]
    assert job["result"]["failed"] == [] and job["result"]["skipped"] == []

    plan = (await _get(env, f"{campaign_id}/plan"))["items"]
    day_of = {p["id"]: (int(p["ordinal"]), p["platform"]) for p in plan}
    assets = (await _content(env, campaign_id=campaign_id))["items"]
    return campaign_id, {day_of[a["plan_item_id"]]: a for a in assets}


async def test_generation_versions_and_qa_gate(env: Env) -> None:
    _, by_item = await _generated(env)
    status = {key: a["status"] for key, a in by_item.items()}
    assert status == {
        (1, "LINKEDIN"): "AWAITING_APPROVAL",
        (2, "X"): "AWAITING_APPROVAL",
        (3, "LINKEDIN"): "AWAITING_APPROVAL",
        (4, "X"): "QA_FAILED",
        (5, "LINKEDIN"): "QA_FAILED",
        (6, "LINKEDIN"): "AWAITING_APPROVAL",
        (7, "X"): "QA_FAILED",
        (8, "LINKEDIN"): "QA_FAILED",
    }, status

    # Gate rejections, each with its machine-readable reason.
    assert "OVER_LENGTH" in _codes(by_item[(4, "X")])
    assert "BANNED_PHRASE" in _codes(by_item[(5, "LINKEDIN")])
    assert "CITATION_NOT_IN_PACKET" in _codes(by_item[(7, "X")])
    assert "MISSING_CTA" in _codes(by_item[(8, "LINKEDIN")])
    # Gate: the near-duplicate pair warns (never blocks) and points at the original.
    dup = by_item[(6, "LINKEDIN")]
    original = by_item[(1, "LINKEDIN")]
    warnings = [f for r in dup["qa"] for f in r["findings"] if f["code"] == "NEAR_DUPLICATE"]
    assert warnings and warnings[0]["severity"] == "WARN"
    assert warnings[0]["detail"]["content_asset_id"] == original["id"]
    assert warnings[0]["detail"]["similarity"] >= 0.9
    assert "NEAR_DUPLICATE" not in _codes(original)  # the first one had nothing to repeat
    similar = await _content(env, f"/{dup['id']}/similar")
    assert [s["content_asset_id"] for s in similar["items"]] == [original["id"]]

    # Every asset: one GENERATED version with provenance and all four QA stages.
    for asset in by_item.values():
        version = asset["current_version"]
        assert version["version_no"] == 1 and version["origin"] == "GENERATED"
        assert version["prompt_version_id"] and version["model_run_id"] and version["agent_run_id"]
        assert version["author_actor_type"] == "AGENT"
        assert sorted(r["stage"] for r in asset["qa"]) == [
            "ACCURACY",
            "BRAND",
            "PLATFORM",
            "STRUCTURAL",
        ]
    thread = by_item[(2, "X")]["current_version"]
    assert thread["body"].count("\n---\n") == 3  # four posts
    async with get_sessionmaker()() as session:
        vectors = (await session.execute(select(Embedding.field, Embedding.dim).distinct())).all()
    assert {(f, d) for f, d in vectors} == {("FULL", 256), ("HOOK", 256)}


async def test_human_edit_creates_version_diff_and_requalifies(env: Env) -> None:
    _, by_item = await _generated(env)
    asset = by_item[(8, "LINKEDIN")]
    assert asset["status"] == "QA_FAILED"
    v1 = asset["current_version"]

    resp = await env.client.post(
        f"/api/v1/content/{asset['id']}/versions",
        headers=env.headers,
        json={
            "based_on_version_id": v1["id"],
            "body": v1["body"],
            "cta": "Book a free 20-minute local-AI readiness call",
            "comment": "added the CTA the plan asks for",
        },
    )
    assert resp.status_code == 201, resp.text
    v2 = resp.json()
    assert v2["version_no"] == 2 and v2["origin"] == "HUMAN_EDIT"
    assert v2["based_on_version_id"] == v1["id"] and v2["author_actor_type"] == "HUMAN"
    assert v2["prompt_version_id"] is None
    assert (await _content(env, f"/{asset['id']}"))["status"] == "GENERATED"

    qa_job = uuid.UUID(resp.headers["X-Job-ID"])
    assert env.queue.enqueued[-1] == ("qa", qa_job)
    assert await execute_job(qa_job, agent_deps=fixture_deps()) is JobStatus.SUCCEEDED
    after = await _content(env, f"/{asset['id']}")
    assert after["status"] == "AWAITING_APPROVAL"
    assert after["current_version_id"] == v2["id"] and "MISSING_CTA" not in _codes(after)

    diff = await _content(env, f"/{asset['id']}/diff")
    assert (diff["from_version_no"], diff["to_version_no"]) == (1, 2)
    assert [c["field"] for c in diff["changes"]] == ["cta"]
    assert diff["changes"][0]["before"] is None
    versions = await _content(env, f"/{asset['id']}/versions")
    assert [v["origin"] for v in versions["items"]] == ["GENERATED", "HUMAN_EDIT"]
    # The v1 QA results are kept (§36 history).
    old_qa = await _content(env, f"/{asset['id']}/qa-results", version_id=v1["id"])
    assert "MISSING_CTA" in {f["code"] for r in old_qa["items"] for f in r["findings"]}

    async with get_sessionmaker()() as session:
        action = (
            await session.execute(
                select(ApprovalAction).where(
                    ApprovalAction.content_asset_id == uuid.UUID(asset["id"])
                )
            )
        ).scalar_one()
    assert action.action == "EDIT" and action.from_status == "QA_FAILED"
    assert action.to_status == "GENERATED" and str(action.resulting_version_id) == v2["id"]

    # A second edit based on the old version is a lost update -> 409.
    stale = await env.client.post(
        f"/api/v1/content/{asset['id']}/versions",
        headers=env.headers,
        json={"based_on_version_id": v1["id"], "body": "stale edit"},
    )
    assert stale.status_code == 409 and stale.json()["code"] == "CONFLICT"
    viewer = await auth_headers(env.client, UserRole.VIEWER)
    denied = await env.client.post(
        f"/api/v1/content/{asset['id']}/versions",
        headers=viewer,
        json={"based_on_version_id": v2["id"], "body": "viewer edit"},
    )
    assert denied.status_code == 403


async def test_generate_assets_requires_an_active_campaign(env: Env) -> None:
    resp = await env.client.post(
        "/api/v1/campaigns/wizard", headers=env.headers, json=_wizard_body(env)
    )
    campaign_id = resp.json()["campaign"]["id"]  # PLANNING, not ACTIVE
    gen = await env.client.post(
        f"/api/v1/campaigns/{campaign_id}/generate-assets", headers=env.headers
    )
    assert gen.status_code == 409
    assert gen.json()["details"]["failed_guard"] == "campaign_active"


class FailingXWriter:
    """Replays fixtures but the X writer is down (transport error on every call)."""

    name = "lmstudio"

    def __init__(self) -> None:
        self._inner = FakeProvider("lmstudio", fixtures_dir=FIXTURES)

    async def aclose(self) -> None:
        return None

    async def list_models(self) -> list[str]:
        return await self._inner.list_models()

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        return await self._inner.embed(model, texts)

    async def chat(self, request: ChatRequest) -> ChatResult:
        if request.meta.get("prompt_id") == "content/x_writer":
            raise OmniGrowthError(ErrorCode.MODEL_UNAVAILABLE, "x writer model not loaded")
        return await self._inner.chat(request)


async def test_failed_drafts_are_claimed_by_the_next_request(env: Env) -> None:
    campaign_id = await _active_campaign(env)
    first = await env.client.post(
        f"/api/v1/campaigns/{campaign_id}/generate-assets", headers=env.headers
    )
    first_job = uuid.UUID(first.json()["id"])
    broken = AgentDeps(router=_router(FailingXWriter()), registry=PromptRegistry.load())
    assert await execute_job(first_job, agent_deps=broken) is JobStatus.SUCCEEDED
    result = (await env.client.get(f"/api/v1/jobs/{first_job}", headers=env.headers)).json()
    assert len(result["result"]["generated"]) == 5 and len(result["result"]["failed"]) == 3
    drafts = (await _content(env, campaign_id=campaign_id, status="DRAFT"))["items"]
    assert len(drafts) == 3 and {a["platform"] for a in drafts} == {"X"}

    second = await env.client.post(
        f"/api/v1/campaigns/{campaign_id}/generate-assets", headers=env.headers
    )
    second_job = uuid.UUID(second.json()["id"])
    assert await execute_job(second_job, agent_deps=fixture_deps()) is JobStatus.SUCCEEDED
    result = (await env.client.get(f"/api/v1/jobs/{second_job}", headers=env.headers)).json()
    assert len(result["result"]["generated"]) == 3 and result["result"]["failed"] == []
    assert (await _content(env, campaign_id=campaign_id, status="DRAFT"))["items"] == []
    assets = (await _content(env, campaign_id=campaign_id))["items"]
    assert len(assets) == 8  # no duplicates were created for the retried plan items
