"""Live planning run against the local LLM (PHASES.md Phase 5 gate, opt-in).

``uv run poe test-live`` (marker ``live_llm`` only, so ``poe test-int`` never runs it). It
needs the compose Postgres and a local OpenAI-compatible endpoint. Lower-quality output is
acceptable; it MUST be schema-valid (A8). The outcome is written to
``docs/audit/phase5_live_planning.json`` for the gate report, pass or fail.
"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from omnigrowth.api.app import create_app
from omnigrowth.api.deps import get_job_queue
from omnigrowth.core.enums import AngleType, JobStatus, UserRole
from omnigrowth.core.settings import get_settings
from omnigrowth.models import AgentRun, ModelRun
from omnigrowth.services.jobs import RecordingJobQueue
from omnigrowth.storage.db import get_engine, get_sessionmaker
from omnigrowth.workflows.tasks import execute_job
from tests.integration.campaign_support import ARTICLE, START, TZ
from tests.integration.helpers import auth_headers

pytestmark = pytest.mark.live_llm

REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "docs" / "audit" / "phase5_live_planning.json"


async def _runs(campaign_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as session:
        agent_runs = (
            await session.execute(
                select(AgentRun)
                .where(AgentRun.campaign_id == campaign_id)
                .order_by(AgentRun.started_at)
            )
        ).scalars()
        out: list[dict[str, Any]] = []
        for run in agent_runs:
            calls = (
                await session.execute(
                    select(ModelRun)
                    .where(ModelRun.agent_run_id == run.id)
                    .order_by(ModelRun.created_at)
                )
            ).scalars()
            out.append(
                {
                    "agent": run.agent_name,
                    "status": str(run.status),
                    "error_code": str(run.error_code) if run.error_code else None,
                    "calls": [
                        {
                            "provider": c.provider,
                            "model": c.model,
                            "attempt_no": c.attempt_no,
                            "mode": c.structured_output_mode,
                            "status": str(c.status),
                            "error_code": str(c.error_code) if c.error_code else None,
                            "latency_ms": c.latency_ms,
                            "output_tokens": c.output_tokens,
                            # Failed calls keep the raw payload (debugging, 1500 chars max).
                            "raw_head": (c.raw_response or "")[:1500] if c.error_code else None,
                        }
                        for c in calls
                    ],
                }
            )
        return out


async def test_live_planning_pipeline(fresh_database: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Use the real LLM endpoint from config/models.yaml (host presets), not a docker URL.
    for key in ("OMNIGROWTH_LOCAL_LLM_URL", "OMNIGROWTH_RUNTIME"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    queue = RecordingJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_queue] = lambda: queue
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        headers = await auth_headers(c, UserRole.EDITOR)
        brand = await c.post(
            "/api/v1/brands",
            headers=headers,
            json={
                "slug": f"live-{secrets.token_hex(3)}",
                "name": "Simply AI Solutions",
                "display_name": "Simply AI Solutions",
                "timezone": TZ,
            },
        )
        assert brand.status_code == 201, brand.text
        wizard = await c.post(
            "/api/v1/campaigns/wizard",
            headers=headers,
            json={
                "brand_id": brand.json()["id"],
                "name": "Local AI for small business",
                "objective": "AUTHORITY",
                "topic": "Why local AI agents matter for small businesses",
                "primary_platform": "LINKEDIN",
                "secondary_platforms": ["X"],
                "start_date": START.isoformat(),
                "timezone": TZ,
                "source_text": ARTICLE,
                "generate": True,
            },
        )
        assert wizard.status_code == 202, wizard.text
        campaign_id = uuid.UUID(wizard.json()["campaign"]["id"])
        job_id = uuid.UUID(wizard.json()["job"]["id"])

        started = time.monotonic()
        status = await execute_job(job_id)  # real router from config/models.yaml
        elapsed = round(time.monotonic() - started, 1)
        job = (await c.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
        campaign = (await c.get(f"/api/v1/campaigns/{campaign_id}", headers=headers)).json()
        ideas: list[dict[str, Any]] = []
        plan: list[dict[str, Any]] = []
        if status is JobStatus.SUCCEEDED:
            ideas = (await c.get(f"/api/v1/campaigns/{campaign_id}/ideas", headers=headers)).json()[
                "items"
            ]
            plan = (await c.get(f"/api/v1/campaigns/{campaign_id}/plan", headers=headers)).json()[
                "items"
            ]
        report = {
            "ran_at": datetime.now(UTC).isoformat(),
            "elapsed_s": elapsed,
            "job_status": str(status),
            "job_error_code": job.get("error_code"),
            "job_result": job.get("result"),
            "campaign_status": campaign["status"],
            "ideas": len(ideas),
            "angles": sorted({i["angle"] for i in ideas}),
            "plan_items": len(plan),
            "plan_days": sorted({p["window_start"][:10] for p in plan}),
            "agent_runs": await _runs(campaign_id),
        }
        REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    await get_engine().dispose()

    assert status is JobStatus.SUCCEEDED, report
    assert campaign["status"] == "READY_FOR_REVIEW"
    assert len(ideas) >= 20 and {i["angle"] for i in ideas} == {a.value for a in AngleType}
    assert len(plan) >= 7 and len(report["plan_days"]) == 7
