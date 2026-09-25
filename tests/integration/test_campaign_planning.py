"""Integration: Phase 5 gate — source -> research packet -> strategy -> >=20 ideas across all
8 angle types -> 7-day plan, all schema-valid, driven by recorded FakeProvider fixtures (A8).

Re-record fixtures after changing a prompt or the canonical answers:

    uv run python scripts/author_canonical_llm.py
    $env:OMNIGROWTH_RECORD_FIXTURES="1"; uv run pytest tests/integration/test_campaign_planning.py
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from omnigrowth.agents.base import AgentDeps
from omnigrowth.core.enums import AngleType, JobStatus, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.models import AgentRun, ContentIdea, ModelRun
from omnigrowth.prompts import PromptRegistry
from omnigrowth.providers.base import ChatRequest, ChatResult
from omnigrowth.providers.fake import FakeProvider
from omnigrowth.storage.db import get_sessionmaker
from omnigrowth.workflows.tasks import execute_job
from tests.integration.campaign_support import (
    ARTICLE,
    FIXTURES,
    START,
    TZ,
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


async def test_planning_pipeline_produces_schema_valid_plan(env: Env) -> None:
    resp = await env.client.post(
        "/api/v1/campaigns/wizard", headers=env.headers, json=_wizard_body(env)
    )
    assert resp.status_code == 202, resp.text
    result = resp.json()
    campaign_id = result["campaign"]["id"]
    assert result["campaign"]["status"] == "PLANNING"
    job_id = uuid.UUID(result["job"]["id"])
    assert env.queue.enqueued == [("agents", job_id)]

    assert await execute_job(job_id, agent_deps=fixture_deps()) is JobStatus.SUCCEEDED
    job = (await env.client.get(f"/api/v1/jobs/{job_id}", headers=env.headers)).json()
    assert job["status"] == "SUCCEEDED", job
    assert job["result"]["ideas"] >= 20 and job["result"]["plan_items"] >= 7

    campaign = await _get(env, campaign_id)
    assert campaign["status"] == "READY_FOR_REVIEW"

    # Research packet (A5): provenance-checked claims, no citations, fabricated quote caught.
    packet = await _get(env, f"{campaign_id}/research-packet")
    assert packet["provider"] == "null"
    claims = packet["claims"]
    assert isinstance(claims, list) and len(claims) == 7
    assert all(c["citation_url"] is None for c in claims)
    by_ordinal = {c["ordinal"]: c for c in claims}
    assert by_ordinal[1]["label"] == "SOURCE_STATED" and by_ordinal[1]["source_excerpt"]
    assert by_ordinal[5]["label"] == "NEEDS_VERIFICATION"  # "80%" is not in the source
    assert by_ordinal[5]["source_excerpt"] is None
    for claim in claims:
        if claim["label"] == "SOURCE_STATED":
            assert " ".join(claim["source_excerpt"].split()).lower() in ARTICLE.lower().replace(
                "\n", " "
            )
    assert job["result"]["claims_downgraded"] == 1

    # Strategy: platforms clamped to the campaign's own choice.
    strategy = await _get(env, f"{campaign_id}/strategy")
    assert strategy["version_no"] == 1 and strategy["is_active"] is True
    assert strategy["primary_platform"] == "LINKEDIN"
    assert strategy["secondary_platforms"] == ["X"]

    # Ideas: >= 20, every angle type, platforms/evidence cleaned, 7 selected.
    ideas = (await _get(env, f"{campaign_id}/ideas"))["items"]
    assert isinstance(ideas, list) and len(ideas) >= 20
    assert {i["angle"] for i in ideas} == {a.value for a in AngleType}
    assert all(set(i["platform_targets"]) <= {"LINKEDIN", "X"} for i in ideas)
    assert all(
        e.get("claim_ordinal", 1) <= 7 for i in ideas for e in i["evidence"] if isinstance(e, dict)
    )
    selected = [i for i in ideas if i["status"] == "SELECTED"]
    assert len(selected) == 7
    assert len({i["angle"] for i in selected}) == 7  # mix spreads across angles

    # 7-day plan: every day covered, windows inside the horizon, links resolved.
    plan = (await _get(env, f"{campaign_id}/plan"))["items"]
    assert isinstance(plan, list) and len(plan) >= 7
    tz = ZoneInfo(TZ)
    days = {
        (datetime.fromisoformat(p["window_start"]).astimezone(tz).date() - START).days for p in plan
    }
    assert days == set(range(7))
    selected_ids = {i["id"] for i in selected}
    plan_ids = {p["id"] for p in plan}
    for item in plan:
        assert item["platform"] in {"LINKEDIN", "X"}
        assert item["content_idea_id"] in selected_ids
        assert datetime.fromisoformat(item["window_end"]) > datetime.fromisoformat(
            item["window_start"]
        )
        assert datetime.fromisoformat(item["window_end"]) - datetime.fromisoformat(
            item["window_start"]
        ) <= timedelta(hours=12)
        if item["depends_on_plan_item_id"] is not None:
            assert item["depends_on_plan_item_id"] in plan_ids
    assert any(p["depends_on_plan_item_id"] for p in plan)

    # Every agent run persisted with its prompt version and model; the repair is recorded.
    async with get_sessionmaker()() as session:
        runs = (
            await session.execute(
                select(AgentRun).where(AgentRun.campaign_id == uuid.UUID(campaign_id))
            )
        ).scalars()
        by_name = {r.agent_name: r for r in runs}
        assert set(by_name) == {"research", "strategy", "opportunity", "planner"}
        for run in by_name.values():
            assert run.status == "SUCCEEDED" and run.prompt_version_id is not None
            assert run.output is not None and run.finished_at is not None
        assert by_name["strategy"].parent_agent_run_id == by_name["research"].id
        attempts = (
            await session.execute(
                select(func.count())
                .select_from(ModelRun)
                .where(ModelRun.agent_run_id == by_name["opportunity"].id)
            )
        ).scalar_one()
        assert attempts == 2  # first answer missed CASE_STUDY -> one repair
        models = set(
            (
                await session.execute(
                    select(ModelRun.model).where(ModelRun.campaign_id == uuid.UUID(campaign_id))
                )
            ).scalars()
        )
        assert models == {"qwen/qwen3.5-9b"}

    # Human review: READY_FOR_REVIEW -> ACTIVE (C7).
    activated = await env.client.post(
        f"/api/v1/campaigns/{campaign_id}/activate", headers=env.headers
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "ACTIVE"


async def test_generate_requires_a_normalized_source(env: Env) -> None:
    body = _wizard_body(env, generate=False)
    body.pop("source_text")
    body.pop("source_title")
    resp = await env.client.post("/api/v1/campaigns/wizard", headers=env.headers, json=body)
    assert resp.status_code == 201, resp.text
    campaign_id = resp.json()["campaign"]["id"]
    gen = await env.client.post(f"/api/v1/campaigns/{campaign_id}/generate", headers=env.headers)
    assert gen.status_code == 409
    assert gen.json()["details"]["failed_guard"] == "has_source"
    assert (await _get(env, campaign_id))["status"] == "DRAFT"


async def test_model_failure_fails_campaign_and_retry_is_allowed(env: Env) -> None:
    resp = await env.client.post(
        "/api/v1/campaigns/wizard", headers=env.headers, json=_wizard_body(env)
    )
    assert resp.status_code == 202, resp.text
    campaign_id = resp.json()["campaign"]["id"]
    job_id = uuid.UUID(resp.json()["job"]["id"])

    down = FakeProvider(
        "lmstudio",
        script=[OmniGrowthError(ErrorCode.MODEL_UNAVAILABLE, "LM Studio is not running")],
    )
    deps = AgentDeps(router=_router(down), registry=PromptRegistry.load())
    assert await execute_job(job_id, agent_deps=deps) is JobStatus.FAILED

    job = (await env.client.get(f"/api/v1/jobs/{job_id}", headers=env.headers)).json()
    assert job["error_code"] == "MODEL_UNAVAILABLE"
    assert (await _get(env, campaign_id))["status"] == "FAILED"
    async with get_sessionmaker()() as session:
        run = (
            await session.execute(
                select(AgentRun).where(AgentRun.campaign_id == uuid.UUID(campaign_id))
            )
        ).scalar_one()
        assert run.agent_name == "research"
        assert run.status == "FAILED" and run.error_code == "MODEL_UNAVAILABLE"

    # FAILED -> PLANNING (C5) and the retry succeeds with a working model.
    retry = await env.client.post(f"/api/v1/campaigns/{campaign_id}/generate", headers=env.headers)
    assert retry.status_code == 202, retry.text
    assert (await _get(env, campaign_id))["status"] == "PLANNING"
    retry_job = uuid.UUID(retry.json()["id"])
    assert await execute_job(retry_job, agent_deps=fixture_deps()) is JobStatus.SUCCEEDED
    assert (await _get(env, campaign_id))["status"] == "READY_FOR_REVIEW"


async def test_viewer_cannot_create_or_generate(env: Env) -> None:
    viewer = await auth_headers(env.client, UserRole.VIEWER)
    resp = await env.client.post("/api/v1/campaigns/wizard", headers=viewer, json=_wizard_body(env))
    assert resp.status_code == 403


class CrashOnPlanner:
    """Replays fixtures but blows up (non-domain error) when the planner prompt runs."""

    name = "lmstudio"

    def __init__(self) -> None:
        self._inner = FakeProvider("lmstudio", fixtures_dir=FIXTURES)

    async def aclose(self) -> None:
        return None

    async def list_models(self) -> list[str]:
        return await self._inner.list_models()

    async def chat(self, request: ChatRequest) -> ChatResult:
        if request.meta.get("prompt_id") == "campaign/planner":
            raise ValueError("library blew up mid-generation")
        return await self._inner.chat(request)


async def test_crash_mid_pipeline_fails_the_running_agent_and_retry_supersedes_mix(
    env: Env,
) -> None:
    resp = await env.client.post(
        "/api/v1/campaigns/wizard", headers=env.headers, json=_wizard_body(env)
    )
    assert resp.status_code == 202, resp.text
    campaign_id = resp.json()["campaign"]["id"]
    job_id = uuid.UUID(resp.json()["job"]["id"])
    crash = AgentDeps(router=_router(CrashOnPlanner()), registry=PromptRegistry.load())
    assert await execute_job(job_id, agent_deps=crash) is JobStatus.FAILED

    job = (await env.client.get(f"/api/v1/jobs/{job_id}", headers=env.headers)).json()
    assert job["error_code"] == "INTERNAL_ERROR"
    assert (await _get(env, campaign_id))["status"] == "FAILED"
    async with get_sessionmaker()() as session:
        runs = {
            r.agent_name: r
            for r in (
                await session.execute(
                    select(AgentRun).where(AgentRun.campaign_id == uuid.UUID(campaign_id))
                )
            ).scalars()
        }
    assert runs["planner"].status == "FAILED" and runs["planner"].error_code == "INTERNAL_ERROR"
    assert runs["planner"].finished_at is not None
    assert all(runs[n].status == "SUCCEEDED" for n in ("research", "strategy", "opportunity"))
    first_mix = [
        i for i in (await _get(env, f"{campaign_id}/ideas"))["items"] if i["status"] == "SELECTED"
    ]
    assert len(first_mix) == 7

    retry = await env.client.post(f"/api/v1/campaigns/{campaign_id}/generate", headers=env.headers)
    assert retry.status_code == 202, retry.text
    assert (
        await execute_job(uuid.UUID(retry.json()["id"]), agent_deps=fixture_deps())
        is JobStatus.SUCCEEDED
    )
    latest = (await _get(env, f"{campaign_id}/ideas"))["items"]
    assert {i["id"] for i in latest}.isdisjoint({i["id"] for i in first_mix})
    assert sum(i["status"] == "SELECTED" for i in latest) == 7
    async with get_sessionmaker()() as session:
        selected_total = (
            await session.execute(
                select(func.count())
                .select_from(ContentIdea)
                .where(
                    ContentIdea.campaign_id == uuid.UUID(campaign_id),
                    ContentIdea.status == "SELECTED",
                )
            )
        ).scalar_one()
    assert selected_total == 7  # the failed run's mix went back to PROPOSED (CI4)
