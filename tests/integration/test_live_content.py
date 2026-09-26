"""Live content run against the local LLM (PHASES.md Phase 6, opt-in, marker ``live_llm``).

Plans a campaign with the real model, activates it, then runs the LinkedIn/X writers, QA and
real embeddings (LM Studio nomic). Content may legitimately fail QA; what must hold is that
every asset gets a schema-valid GENERATED version, all four QA stages and an embedding.
The outcome is written to ``docs/audit/phase6_live_content.json`` pass or fail.
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
from omnigrowth.core.enums import JobStatus, UserRole
from omnigrowth.core.settings import get_settings
from omnigrowth.models import Embedding
from omnigrowth.services.jobs import RecordingJobQueue
from omnigrowth.storage.db import get_engine, get_sessionmaker
from omnigrowth.workflows.tasks import execute_job
from tests.integration.campaign_support import ARTICLE, START, TZ
from tests.integration.helpers import IK_HOOKS, auth_headers

pytestmark = pytest.mark.live_llm

REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "docs" / "audit" / "phase6_live_content.json"


async def test_live_content_pipeline(fresh_database: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OMNIGROWTH_LOCAL_LLM_URL", "OMNIGROWTH_RUNTIME"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    queue = RecordingJobQueue()
    app = create_app()
    app.dependency_overrides[get_job_queue] = lambda: queue
    report: dict[str, Any] = {"ran_at": datetime.now(UTC).isoformat()}
    async with httpx.AsyncClient(
        event_hooks=IK_HOOKS, transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        headers = await auth_headers(c, UserRole.EDITOR)
        brand = await c.post(
            "/api/v1/brands",
            headers=headers,
            json={
                "slug": f"live-{secrets.token_hex(3)}",
                "name": "Simply AI Solutions",
                "display_name": "Simply AI Solutions",
                "timezone": TZ,
                "profile": {"banned_phrases": ["game-changer"]},
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
        campaign_id = wizard.json()["campaign"]["id"]
        started = time.monotonic()
        planned = await execute_job(uuid.UUID(wizard.json()["job"]["id"]))
        report["planning_s"] = round(time.monotonic() - started, 1)
        report["planning_status"] = str(planned)
        assert planned is JobStatus.SUCCEEDED, report
        assert (
            await c.post(f"/api/v1/campaigns/{campaign_id}/activate", headers=headers)
        ).status_code == 200

        gen = await c.post(f"/api/v1/campaigns/{campaign_id}/generate-assets", headers=headers)
        assert gen.status_code == 202, gen.text
        started = time.monotonic()
        generated = await execute_job(uuid.UUID(gen.json()["id"]))
        report["generation_s"] = round(time.monotonic() - started, 1)
        job = (await c.get(f"/api/v1/jobs/{gen.json()['id']}", headers=headers)).json()
        report["generation_status"] = str(generated)
        report["generation_result"] = job.get("result")
        report["generation_error"] = job.get("error_code")
        assets = (
            await c.get("/api/v1/content", headers=headers, params={"campaign_id": campaign_id})
        ).json()["items"]
        report["assets"] = [
            {
                "platform": a["platform"],
                "format": a["format"],
                "status": a["status"],
                "quality_score": a["quality_score"],
                "chars": a["current_version"]["char_count"] if a["current_version"] else None,
                "findings": sorted(
                    f"{r['stage']}:{f['severity']}:{f['code']}"
                    for r in a["qa"]
                    for f in r["findings"]
                ),
            }
            for a in assets
        ]
        async with get_sessionmaker()() as session:
            dims = (
                await session.execute(select(Embedding.embedding_model, Embedding.dim).distinct())
            ).all()
        report["embeddings"] = [{"model": m, "dim": d} for m, d in dims]
        REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    await get_engine().dispose()

    assert generated is JobStatus.SUCCEEDED, report
    assert assets and all(a["current_version"] for a in assets), report
    for asset in assets:
        assert asset["status"] in {"AWAITING_APPROVAL", "QA_FAILED"}, report
        assert len(asset["qa"]) == 4
    assert report["embeddings"], report
