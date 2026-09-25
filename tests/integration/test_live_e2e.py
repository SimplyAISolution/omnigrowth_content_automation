"""§55 success scenario against the local LLM (Phase 7 gate: "live_llm E2E run once").

Same flow as test_e2e_success_scenario.py, but every agent (research, strategy, opportunity,
planner, writers, optimizer) and the embeddings use the real models from config/models.yaml;
publishing stays MOCK. Model output legitimately varies, so content-level expectations are
relaxed: what must hold is that every step reaches its state and nothing is faked. The
outcome is written to ``docs/audit/phase7_live_e2e.json`` pass or fail.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omnigrowth.core.enums import JobStatus, UserRole
from omnigrowth.core.settings import get_settings
from omnigrowth.publishing.config import load_publishing_config
from omnigrowth.publishing.mock import MockPublisher
from omnigrowth.publishing.scheduler import run_once
from omnigrowth.workflows.tasks import execute_job
from tests.integration.campaign_support import ARTICLE, START, TZ, Env
from tests.integration.helpers import auth_headers

pytestmark = pytest.mark.live_llm

REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "docs" / "audit" / "phase7_live_e2e.json"
TOPIC = "Local AI agents will become an important alternative to fully cloud-hosted AI."


@pytest.fixture
def env(campaign_env: Env, monkeypatch: pytest.MonkeyPatch) -> Env:
    for key in ("OMNIGROWTH_LOCAL_LLM_URL", "OMNIGROWTH_RUNTIME"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    return campaign_env


async def _ok(env: Env, method: str, path: str, expect: int = 200, **kw: Any) -> Any:
    resp = await env.client.request(method, f"/api/v1{path}", headers=env.headers, **kw)
    assert resp.status_code == expect, f"{method} {path}: {resp.status_code} {resp.text}"
    return resp.json()


async def test_live_success_scenario(env: Env) -> None:
    report: dict[str, Any] = {"ran_at": datetime.now(UTC).isoformat(), "steps": {}}
    steps: dict[str, Any] = report["steps"]

    async def run(name: str, job_id: str) -> dict[str, Any]:
        started = time.monotonic()
        status = await execute_job(uuid.UUID(job_id))  # real router, real models
        job: dict[str, Any] = await _ok(env, "GET", f"/jobs/{job_id}")
        steps[name] = {
            "status": str(status),
            "seconds": round(time.monotonic() - started, 1),
            "error_code": job.get("error_code"),
        }
        REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        assert status is JobStatus.SUCCEEDED, steps[name]
        return job

    try:
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
                    "handle": f"live-{platform.lower()}",
                },
            )
            assert resp.status_code == 201, resp.text
            accounts[platform] = resp.json()["id"]
        source = await _ok(
            env,
            "POST",
            "/sources/text",
            201,
            json={"brand_id": env.brand_id, "source_type": "TEXT", "content": ARTICLE},
        )
        audience_id = (await _ok(env, "GET", f"/brands/{env.brand_id}/audiences"))["items"][0]["id"]
        wizard = await _ok(
            env,
            "POST",
            "/campaigns/wizard",
            202,
            json={
                "brand_id": env.brand_id,
                "name": "Local AI authority (live)",
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
        await run("planning", wizard["job"]["id"])
        plan = (await _ok(env, "GET", f"/campaigns/{campaign_id}/plan"))["items"]
        steps["plan"] = sorted({p["platform"] for p in plan})
        await _ok(env, "POST", f"/campaigns/{campaign_id}/activate")

        gen = await _ok(env, "POST", f"/campaigns/{campaign_id}/generate-assets", 202)
        job = await run("generation", gen["id"])
        steps["generation_result"] = {
            "generated": len(job["result"]["generated"]),
            "failed": job["result"]["failed"],
            "skipped": sorted({s["platform"] for s in job["result"]["skipped"]}),
        }
        assets = (await _ok(env, "GET", "/content", params={"campaign_id": campaign_id}))["items"]
        steps["assets"] = [
            {
                "platform": a["platform"],
                "format": a["format"],
                "status": a["status"],
                "fails": sorted(
                    f["code"] for r in a["qa"] for f in r["findings"] if f["severity"] == "FAIL"
                ),
            }
            for a in assets
        ]
        queue = (await _ok(env, "GET", "/approvals", params={"campaign_id": campaign_id}))["items"]
        steps["approval_queue"] = len(queue)
        assert queue, "no generated asset passed QA; nothing to approve"

        due = datetime.now(UTC) + timedelta(minutes=5)
        for asset in queue:
            approved = await _ok(
                env,
                "POST",
                f"/approvals/{asset['id']}/actions",
                json={"action": "APPROVE", "content_version_id": asset["current_version_id"]},
            )
            await _ok(
                env,
                "POST",
                f"/approvals/{asset['id']}/actions",
                json={
                    "action": "SCHEDULE",
                    "content_version_id": approved["asset"]["approved_version_id"],
                    "platform_account_id": accounts[asset["platform"]],
                    "scheduled_at": due.isoformat(),
                },
            )
        tick = await run_once(
            publisher=MockPublisher(),
            config=load_publishing_config(REPO / "config"),
            now=due + timedelta(seconds=1),
        )
        pubs = (await _ok(env, "GET", "/publications", params={"campaign_id": campaign_id}))[
            "items"
        ]
        steps["publishing"] = {
            "claimed": tick.claimed,
            "succeeded": tick.succeeded,
            "published": sum(p["status"] == "PUBLISHED" for p in pubs),
        }
        assert steps["publishing"]["published"] == len(queue)

        pull = await _ok(env, "POST", "/analytics/pull", 202, json={"campaign_id": campaign_id})
        await run("analytics_pull", pull["id"])
        analytics = await _ok(env, "GET", f"/analytics/campaigns/{campaign_id}/report")
        steps["report"] = {"coverage": analytics["coverage"], "derived": analytics["derived"]}

        recs_job = await _ok(
            env, "POST", "/recommendations/generate", 202, json={"campaign_id": campaign_id}
        )
        rec_result = await run("recommendations", recs_job["id"])
        recs = (await _ok(env, "GET", "/recommendations", params={"campaign_id": campaign_id}))[
            "items"
        ]
        snapshots = {
            s["id"]
            for s in (
                await _ok(env, "GET", "/analytics/snapshots", params={"campaign_id": campaign_id})
            )["items"]
        }
        steps["recommendations"] = {
            "stored": len(recs),
            "dropped": len(rec_result["result"]["dropped"]),
            "statements": [r["statement"] for r in recs],
        }
        assert recs and all(
            r["evidence_snapshot_ids"] and set(r["evidence_snapshot_ids"]) <= snapshots
            for r in recs
        )
        report["outcome"] = "PASSED"
    except AssertionError as exc:
        report["outcome"] = f"FAILED: {exc}"[:2000]
        raise
    finally:
        REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
