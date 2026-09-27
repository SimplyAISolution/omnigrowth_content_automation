"""Shared campaign test environment (planning, content): app client, brand, fixtures."""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from omnigrowth.agents.base import AgentDeps
from omnigrowth.api.app import create_app
from omnigrowth.api.deps import get_job_queue
from omnigrowth.core.enums import UserRole
from omnigrowth.prompts import PromptRegistry
from omnigrowth.providers.base import ChatProvider
from omnigrowth.providers.config import load_models_config
from omnigrowth.providers.fake import CannedProvider, FakeProvider
from omnigrowth.providers.router import ModelRouter
from omnigrowth.services.jobs import RecordingJobQueue
from omnigrowth.storage.db import get_engine
from tests.integration.helpers import IK_HOOKS, auth_headers

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "llm"
CANONICAL = REPO / "tests" / "fixtures" / "llm_canonical"
START = date(2026, 10, 5)
TZ = "America/New_York"

ARTICLE = (
    "# Why Local AI Agents Will Matter for Small Businesses\n\n"
    "Local AI agents keep proprietary data on the machine instead of sending it to a cloud "
    "provider.\n\n"
    "Running a 9B-parameter model on a consumer GPU with 12 GB of VRAM is now practical for "
    "drafting and classification work.\n\n"
    "Routine work such as summarising support tickets avoids per-token cloud fees when it "
    "runs locally.\n\n"
    "Local models are still weaker than frontier models at long, multi-step reasoning.\n\n"
    "We believe most small teams will run a hybrid setup within two years.\n"
)


def _router(provider: ChatProvider) -> ModelRouter:
    """Repo model config, but for FIXTURE replay: a FakeProvider has no tokenizer and no
    server to query, so budget counting is the labelled approximate estimate and there is
    no server idle check (both are verified against LM Studio in tests/live/)."""
    config = load_models_config(REPO / "config")
    replay = {
        name: p.model_copy(update={"budget_counting": "approximate", "idle_check": "none"})
        for name, p in config.providers.items()
    }
    return ModelRouter(config.model_copy(update={"providers": replay}), {"lmstudio": provider})


def fixture_deps() -> AgentDeps:
    record = os.environ.get("OMNIGROWTH_RECORD_FIXTURES") == "1"
    provider = FakeProvider(
        "lmstudio",
        fixtures_dir=FIXTURES,
        record_from=CannedProvider(CANONICAL) if record else None,
    )
    return AgentDeps(router=_router(provider), registry=PromptRegistry.load())


class Env:
    client: httpx.AsyncClient
    queue: RecordingJobQueue
    headers: dict[str, str]
    brand_id: str
    source_id: str


@asynccontextmanager
async def open_campaign_env() -> AsyncIterator[Env]:
    """Editor + brand (with profile) + primary audience; queue captured, not run."""
    e = Env()
    e.queue = RecordingJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_queue] = lambda: e.queue
    async with httpx.AsyncClient(
        event_hooks=IK_HOOKS, transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        e.client = c
        e.headers = await auth_headers(c, UserRole.EDITOR)
        brand = await c.post(
            "/api/v1/brands",
            headers=e.headers,
            json={
                "slug": f"simply-ai-{secrets.token_hex(3)}",
                "name": "Simply AI Solutions",
                "display_name": "Simply AI Solutions",
                "timezone": TZ,
                "profile": {
                    "niche": "Private, local-first AI for small businesses",
                    "differentiators": ["Runs on hardware you own"],
                    "content_pillars": ["Data privacy", "AI costs"],
                    "banned_phrases": ["game-changer"],
                },
            },
        )
        assert brand.status_code == 201, brand.text
        e.brand_id = brand.json()["id"]
        audience = await c.post(
            f"/api/v1/brands/{e.brand_id}/audiences",
            headers=e.headers,
            json={
                "name": "Small-business owners",
                "is_primary": True,
                "pains": ["Rising SaaS and AI fees", "Client data privacy"],
                "common_questions": ["Is local AI good enough?"],
            },
        )
        assert audience.status_code == 201, audience.text
        yield e
    await get_engine().dispose()


def _wizard_body(env: Env, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "brand_id": env.brand_id,
        "name": "Local AI for small business",
        "objective": "AUTHORITY",
        "topic": "Why local AI agents matter for small businesses",
        "primary_platform": "LINKEDIN",
        "secondary_platforms": ["X"],
        "start_date": START.isoformat(),
        "timezone": TZ,
        "source_text": ARTICLE,
        "source_title": "Why Local AI Agents Will Matter for Small Businesses",
        "generate": True,
    }
    body.update(overrides)
    return body


async def _get(env: Env, path: str) -> Any:
    resp = await env.client.get(f"/api/v1/campaigns/{path}", headers=env.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def generated_campaign(env: Env) -> str:
    """Planned (fixtures), activated campaign whose assets went through writers + QA."""
    from omnigrowth.core.enums import JobStatus
    from omnigrowth.workflows.tasks import execute_job

    resp = await env.client.post(
        "/api/v1/campaigns/wizard", headers=env.headers, json=_wizard_body(env)
    )
    assert resp.status_code == 202, resp.text
    campaign_id = str(resp.json()["campaign"]["id"])
    planning = uuid.UUID(resp.json()["job"]["id"])
    assert await execute_job(planning, agent_deps=fixture_deps()) is JobStatus.SUCCEEDED
    activated = await env.client.post(
        f"/api/v1/campaigns/{campaign_id}/activate", headers=env.headers
    )
    assert activated.status_code == 200, activated.text
    gen = await env.client.post(
        f"/api/v1/campaigns/{campaign_id}/generate-assets", headers=env.headers
    )
    assert gen.status_code == 202, gen.text
    job = uuid.UUID(gen.json()["id"])
    assert await execute_job(job, agent_deps=fixture_deps()) is JobStatus.SUCCEEDED
    return campaign_id


async def approve_all(env: Env, campaign_id: str) -> list[dict[str, Any]]:
    """APPROVE every asset waiting for approval; returns the approved assets."""
    queue = await env.client.get(
        "/api/v1/approvals", headers=env.headers, params={"campaign_id": campaign_id}
    )
    assert queue.status_code == 200, queue.text
    approved = []
    for asset in queue.json()["items"]:
        resp = await env.client.post(
            f"/api/v1/approvals/{asset['id']}/actions",
            headers=env.headers,
            json={"action": "APPROVE", "content_version_id": asset["current_version_id"]},
        )
        assert resp.status_code == 200, resp.text
        approved.append(resp.json()["asset"])
    return approved


async def platform_accounts(env: Env, *platforms: str) -> dict[str, str]:
    """One mock account per platform (OWNER creates them); platform -> account id."""
    from omnigrowth.core.enums import UserRole

    owner = await auth_headers(env.client, UserRole.OWNER)
    ids: dict[str, str] = {}
    for platform in platforms:
        resp = await env.client.post(
            "/api/v1/platform-accounts",
            headers=owner,
            json={
                "brand_id": env.brand_id,
                "platform": platform,
                "account_type": "ORGANIZATION",
                "handle": f"simplyai-{platform.lower()}",
                "timezone": TZ,
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["live_enabled"] is False
        ids[platform] = str(resp.json()["id"])
    return ids
