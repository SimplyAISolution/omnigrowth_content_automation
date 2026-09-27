"""§55 success scenario against the local LLM (Phase 7 gate: "live_llm E2E run once").

Same flow as test_e2e_success_scenario.py, but every agent (research, strategy, opportunity,
planner, writers, optimizer) and the embeddings use the real models from config/models.yaml;
publishing stays MOCK. Model output legitimately varies, so content-level expectations are
relaxed: what must hold is that every step reaches its state and nothing is faked.

Every run writes a NEW record ``docs/audit/live/live_e2e_<UTC timestamp>.json`` (pass or fail;
historical records are never overwritten) labelled "real local inference with mock
publication", with the LM Studio load state (models, quantization, context) and one row per
model call (task, model, input/output tokens, latency, throughput) read from ``model_runs``.
A pass does NOT establish real-platform publishing readiness. Since Phase 10 the INSTAGRAM
and YOUTUBE items are written by their own writers (until then they were skipped, decision
D2); publication of every platform stays MOCK.
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
AUDIT_DIR = REPO / "docs" / "audit" / "live"
LABEL = "real local inference with mock publication"
EXCLUDED: list[str] = []  # Phase 10: every platform has a writer (was INSTAGRAM, YOUTUBE)
NEW_WRITERS = ("YOUTUBE", "INSTAGRAM")  # must be attempted by their writers, not skipped
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


def _server_state() -> dict[str, Any]:
    """LM Studio's own view of what is loaded (model, quantization, context)."""
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:1234/api/v0/models", timeout=5) as r:
            data = json.load(r)["data"]
    except OSError as exc:
        return {"error": type(exc).__name__}
    return {
        "loaded": [
            {
                "id": m["id"],
                "quantization": m.get("quantization"),
                "loaded_context_length": m.get("loaded_context_length"),
                "arch": m.get("arch"),
            }
            for m in data
            if m.get("state") == "loaded"
        ]
    }


async def _model_calls(since: datetime) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from omnigrowth.models import ModelRun
    from omnigrowth.storage.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(ModelRun).where(ModelRun.created_at >= since).order_by(ModelRun.created_at)
            )
        ).scalars()
        return [
            {
                "task": r.task,
                "model": r.model,
                "status": str(r.status),
                "error_code": str(r.error_code) if r.error_code else None,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "latency_s": round(r.latency_ms / 1000, 1) if r.latency_ms else None,
                # End-to-end: output tokens / whole call latency (includes prompt processing).
                "output_tok_per_s_end_to_end": round(r.output_tokens / (r.latency_ms / 1000), 1)
                if r.output_tokens and r.latency_ms
                else None,
            }
            for r in rows
        ]


async def test_live_success_scenario(env: Env) -> None:
    started_at = datetime.now(UTC)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = AUDIT_DIR / f"live_e2e_{started_at:%Y%m%dT%H%M%S%fZ}.json"
    report_path.touch(exist_ok=False)  # claim the name: a record is never overwritten
    report: dict[str, Any] = {
        "ran_at": started_at.isoformat(),
        "label": LABEL,
        "publishing": "MockPublisher (PUBLISH_MODE=MOCK): no platform call, no real audience",
        "excluded_platforms": EXCLUDED,
        "not_established": "real-platform publishing readiness",
        "server_before": _server_state(),
        "outcome": "INCOMPLETE (the run stopped before recording an outcome)",
        "steps": {},
    }
    steps: dict[str, Any] = report["steps"]

    def write() -> None:
        report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    async def run(name: str, job_id: str) -> dict[str, Any]:
        started = time.monotonic()
        status = await execute_job(uuid.UUID(job_id))  # real router, real models
        job: dict[str, Any] = await _ok(env, "GET", f"/jobs/{job_id}")
        steps[name] = {
            "status": str(status),
            "seconds": round(time.monotonic() - started, 1),
            "error_code": job.get("error_code"),
        }
        write()
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
        assert steps["generation_result"]["skipped"] == [], steps["generation_result"]
        assets = (await _ok(env, "GET", "/content", params={"campaign_id": campaign_id}))["items"]
        # The planned YOUTUBE/INSTAGRAM items reached their writers (their QA outcome may
        # legitimately vary with model output; it is recorded below, not assumed).
        written = {a["platform"] for a in assets if a["current_version_id"]}
        planned = {p["platform"] for p in plan}
        assert {p for p in NEW_WRITERS if p in planned} <= written, (planned, written)
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
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["server_after"] = _server_state()
        calls = await _model_calls(started_at)
        report["model_calls"] = calls
        timed = [c for c in calls if c["output_tokens"] and c["latency_s"]]
        report["throughput"] = {
            "calls": len(calls),
            "output_tokens": sum(c["output_tokens"] or 0 for c in calls),
            "input_tokens": sum(c["input_tokens"] or 0 for c in calls),
            "model_seconds": round(sum(c["latency_s"] or 0 for c in calls), 1),
            "mean_output_tok_per_s_end_to_end": round(
                sum(c["output_tokens"] for c in timed) / sum(c["latency_s"] for c in timed), 1
            )
            if timed
            else None,
        }
        write()
