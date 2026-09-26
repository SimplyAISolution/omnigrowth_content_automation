"""Integration: Phase 8 — analytics depth.

Derived-metric definitions (data, revisions, OWNER-only, formula validation), per-publication
metrics, attribute comparisons (§16) with honest ``sufficient`` flags and NULL exclusion,
campaign report citing definition revisions and data provenance (simulated vs real),
calendar view, scheduled analytics pulls, and [IK] on snapshot ingest.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from omnigrowth.core.enums import ActorType, JobKind, UserRole
from omnigrowth.models import Job
from omnigrowth.publishing.config import AnalyticsPullConfig, load_publishing_config
from omnigrowth.publishing.mock import MockPublisher
from omnigrowth.publishing.scheduler import run_once
from omnigrowth.services.analytics import schedule_due_pulls
from omnigrowth.services.jobs import RecordingJobQueue
from omnigrowth.storage.db import get_sessionmaker
from tests.integration.campaign_support import (
    Env,
    approve_all,
    generated_campaign,
    platform_accounts,
)
from tests.integration.helpers import SKIP_IK, auth_headers

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
CONFIG = load_publishing_config(REPO / "config")


@pytest.fixture
def env(campaign_env: Env) -> Env:
    return campaign_env


async def _get(env: Env, path: str, headers: dict[str, str] | None = None, **params: Any) -> Any:
    resp = await env.client.get(f"/api/v1{path}", headers=headers or env.headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


class Published:
    campaign_id: str
    due: datetime
    publications: list[dict[str, Any]]
    job_ids: list[str]


async def _published(env: Env) -> Published:
    out = Published()
    out.campaign_id = await generated_campaign(env)
    assets = await approve_all(env, out.campaign_id)
    accounts = await platform_accounts(env, "LINKEDIN", "X")
    out.due = datetime.now(UTC) + timedelta(hours=1)
    out.job_ids = []
    for asset in assets:
        if asset["platform"] not in accounts:
            continue
        resp = await env.client.post(
            f"/api/v1/approvals/{asset['id']}/actions",
            headers=env.headers,
            json={
                "action": "SCHEDULE",
                "content_version_id": asset["approved_version_id"],
                "platform_account_id": accounts[asset["platform"]],
                "scheduled_at": out.due.isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        out.job_ids.append(resp.json()["publication_job"]["id"])
    await run_once(
        publisher=MockPublisher(),
        config=CONFIG,
        now=out.due + timedelta(seconds=1),
        scheduler_id="phase8",
    )
    pubs = (await _get(env, "/publications", campaign_id=out.campaign_id))["items"]
    out.publications = sorted(
        (p for p in pubs if p["status"] == "PUBLISHED"), key=lambda p: p["id"]
    )
    assert len(out.publications) >= 3
    return out


async def _ingest(env: Env, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    resp = await env.client.post(
        "/api/v1/analytics/snapshots", headers=env.headers, json={"items": snapshots}
    )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def _snap(pub: dict[str, Any], captured_at: datetime, **metrics: int | None) -> dict[str, Any]:
    return {
        "publication_id": pub["id"],
        "captured_at": captured_at.isoformat(),
        "source": "FIXTURE",
        **metrics,
    }


# ------------------------------------------------------------------ definitions (§17)
async def test_definitions_are_data_with_revisions_and_owner_only_edits(env: Env) -> None:
    defaults = (await _get(env, "/analytics/derived-metric-definitions"))["items"]
    by_key = {d["key"]: d for d in defaults if d["is_default"]}
    assert set(by_key) >= {
        "engagement_rate",
        "share_rate",
        "save_rate",
        "comment_rate",
        "click_rate",
        "follow_conversion",
        "content_efficiency",
    }
    assert by_key["click_rate"]["formula"] == "clicks / impressions"
    assert by_key["content_efficiency"]["weights"]["conversion"] == 5.0

    body = {
        "brand_id": env.brand_id,
        "key": "save_rate",
        "name": "Save rate (brand)",
        "formula": "saves / reach",
    }
    editor = await env.client.post(
        "/api/v1/analytics/derived-metric-definitions", headers=env.headers, json=body
    )
    assert editor.status_code == 403

    owner = await auth_headers(env.client, UserRole.OWNER)
    bad = await env.client.post(
        "/api/v1/analytics/derived-metric-definitions",
        headers=owner,
        json={**body, "formula": "__import__('os').getcwd()"},
    )
    assert bad.status_code == 422, bad.text
    assert bad.json()["details"]["reason"] == "INVALID_FORMULA"

    created = await env.client.post(
        "/api/v1/analytics/derived-metric-definitions", headers=owner, json=body
    )
    assert created.status_code == 201, created.text
    first = created.json()
    assert first["revision"] == 1 and first["is_default"] is False
    again = await env.client.post(
        "/api/v1/analytics/derived-metric-definitions", headers=owner, json=body
    )
    assert again.status_code == 409

    revised = await env.client.patch(
        f"/api/v1/analytics/derived-metric-definitions/{first['id']}",
        headers=owner,
        json={"formula": "w.boost * saves / reach", "weights": {"boost": 2.0}},
    )
    assert revised.status_code == 200, revised.text
    second = revised.json()
    assert second["revision"] == 2 and second["id"] != first["id"]
    stale = await env.client.patch(
        f"/api/v1/analytics/derived-metric-definitions/{first['id']}",
        headers=owner,
        json={"name": "x"},
    )
    assert stale.status_code == 409

    history = (
        await _get(
            env,
            "/analytics/derived-metric-definitions",
            brand_id=env.brand_id,
            include_history="true",
        )
    )["items"]
    brand_rows = [d for d in history if d["key"] == "save_rate" and not d["is_default"]]
    assert [(d["revision"], d["is_active"]) for d in brand_rows] == [(1, False), (2, True)]
    active = (await _get(env, "/analytics/derived-metric-definitions", brand_id=env.brand_id))[
        "items"
    ]
    save = [d for d in active if d["key"] == "save_rate"]
    # The brand override shadows the default only in the brand's resolution (report below).
    assert {d["id"] for d in save} == {by_key["save_rate"]["id"], second["id"]}


# ------------------------------------------------------------------ metrics + comparisons
async def test_publication_metrics_comparisons_and_report(env: Env) -> None:
    ready = await _published(env)
    pubs = ready.publications
    t0 = ready.due + timedelta(hours=2)
    # Hand-set fixture values. pubs[0] has no impressions (rates must be NULL, not 0).
    values = [
        {"impressions": None, "clicks": 7, "likes": 3},
        {"impressions": 1_000, "clicks": 20, "likes": 40, "comments": 10},
        {"impressions": 500, "clicks": 5, "likes": 10, "comments": 0},
    ]
    values += [{"impressions": 100, "clicks": 1} for _ in pubs[3:]]
    snapshots = [_snap(p, t0, **v) for p, v in zip(pubs, values, strict=True)]
    result = await _ingest(env, snapshots)
    assert len(result["created"]) == len(pubs) and result["duplicates"] == 0

    # -- per publication
    first = await _get(env, f"/analytics/publications/{pubs[0]['id']}/metrics")
    click = next(d for d in first["derived"] if d["key"] == "click_rate")
    assert click["value"] is None and click["null_code"] == "MISSING_INPUT"
    assert click["null_reason"] == "impressions not captured"
    assert first["raw"]["impressions"] is None and first["raw"]["clicks"] == 7
    assert first["provenance"]["simulated"] is True and first["provenance"]["real"] is False
    second = await _get(env, f"/analytics/publications/{pubs[1]['id']}/metrics")
    by_key = {d["key"]: d for d in second["derived"]}
    assert by_key["click_rate"]["value"] == pytest.approx(0.02)  # 20 / 1000
    assert by_key["engagement_rate"]["value"] == pytest.approx(0.05)  # (40+10) / 1000
    assert set(by_key["engagement_rate"]["missing_inputs"]) == {"shares", "saves"}
    assert by_key["click_rate"]["revision"] == 1

    # -- attribute comparison: click_rate by platform
    platforms = {p["id"]: p["platform"] for p in pubs}
    comparison = await _get(
        env,
        "/analytics/attribute-comparisons",
        brand_id=env.brand_id,
        campaign_id=ready.campaign_id,
        attribute="platform",
        metric="click_rate",
        min_sample=2,
    )
    expected: dict[str, list[float]] = {}
    excluded: dict[str, int] = {}
    for pub, v in zip(pubs, values, strict=True):
        if v["impressions"] is None:
            excluded[platforms[pub["id"]]] = excluded.get(platforms[pub["id"]], 0) + 1
        else:
            expected.setdefault(platforms[pub["id"]], []).append(v["clicks"] / v["impressions"])
    groups = {g["value"]: g for g in comparison["groups"]}
    assert set(groups) == set(expected) | set(excluded)
    for platform, rates in expected.items():
        group = groups[platform]
        assert group["n"] == len(rates)
        assert group["mean"] == pytest.approx(sum(rates) / len(rates))
        assert group["sufficient"] is (len(rates) >= 2)
        assert group["sources"] == {"FIXTURE": len(rates)}
    for platform, count in excluded.items():
        assert groups[platform]["excluded_null"] == count
    assert "not causal" in comparison["note"]
    assert comparison["provenance"]["simulated"] is True
    assert "Simulated" in comparison["provenance"]["note"]
    assert comparison["definition"]["key"] == "click_rate"

    raw = await _get(
        env,
        "/analytics/attribute-comparisons",
        brand_id=env.brand_id,
        campaign_id=ready.campaign_id,
        attribute="word_count_bucket",
        metric="clicks",
    )
    assert sum(g["n"] for g in raw["groups"]) == len(pubs)  # clicks captured everywhere
    assert raw["definition"] is None

    for params, field in (
        ({"attribute": "colour", "metric": "click_rate"}, "attribute"),
        ({"attribute": "platform", "metric": "vibes"}, "metric"),
    ):
        bad = await env.client.get(
            "/api/v1/analytics/attribute-comparisons",
            headers=env.headers,
            params={"brand_id": env.brand_id, **params},
        )
        assert bad.status_code == 422 and bad.json()["details"]["field"] == field

    # Only real (API/MANUAL) data requested -> nothing simulated leaks in.
    real_only = await _get(
        env,
        "/analytics/attribute-comparisons",
        brand_id=env.brand_id,
        campaign_id=ready.campaign_id,
        attribute="platform",
        metric="click_rate",
        sources=["API", "MANUAL"],
    )
    assert real_only["groups"] == [] and real_only["provenance"]["simulated"] is False

    # -- campaign report
    report = await _get(env, f"/analytics/campaigns/{ready.campaign_id}/report", min_sample=2)
    assert report["coverage"] == {"published": len(pubs), "with_snapshot": len(pubs)}
    assert report["provenance"]["sources"] == {"FIXTURE": len(pubs)}
    assert report["provenance"]["simulated"] is True
    # Aggregate click rate skips pubs[0] (no impressions): sum clicks / sum impressions.
    usable = [v for v in values if v["impressions"] is not None]
    assert report["derived"]["click_rate"]["value"] == pytest.approx(
        round(sum(v["clicks"] for v in usable) / sum(v["impressions"] for v in usable), 6)
    )
    assert report["totals"]["saves"] is None  # nobody captured saves: NULL, not 0
    assert set(report["by_format"]) and report["sufficiency"]["satisfied"] is True
    cited = {d["key"]: d for d in report["definitions"]}
    assert cited["click_rate"]["revision"] == 1 and cited["click_rate"]["scope"] == "default"
    top = set(report["sufficiency"]["top_publication_ids"])
    assert not top & set(report["sufficiency"]["bottom_publication_ids"])

    # A window that ends before the snapshots -> no data, honest coverage.
    early = await _get(
        env,
        f"/analytics/campaigns/{ready.campaign_id}/report",
        until=(t0 - timedelta(minutes=1)).isoformat(),
    )
    assert early["coverage"]["with_snapshot"] == 0
    assert early["derived"]["click_rate"]["value"] is None


async def test_report_uses_the_brand_revision_of_a_definition(env: Env) -> None:
    ready = await _published(env)
    t0 = ready.due + timedelta(hours=3)
    await _ingest(
        env,
        [_snap(p, t0, impressions=1_000, clicks=10, reach=800) for p in ready.publications],
    )
    owner = await auth_headers(env.client, UserRole.OWNER)
    created = await env.client.post(
        "/api/v1/analytics/derived-metric-definitions",
        headers=owner,
        json={
            "brand_id": env.brand_id,
            "key": "click_rate",
            "name": "Click rate per reach",
            "formula": "clicks / reach",
        },
    )
    assert created.status_code == 201, created.text
    report = await _get(env, f"/analytics/campaigns/{ready.campaign_id}/report")
    cited = {d["key"]: d for d in report["definitions"]}
    assert cited["click_rate"]["definition_id"] == created.json()["id"]
    assert cited["click_rate"]["scope"] == "brand"
    assert report["derived"]["click_rate"]["value"] == pytest.approx(0.0125)  # 10 / 800


# ------------------------------------------------------------------ calendar (§13)
async def test_calendar_shows_jobs_and_unscheduled_plan_items(env: Env) -> None:
    campaign_id = await generated_campaign(env)
    plan = await _get(env, f"/campaigns/{campaign_id}/plan")
    items = plan["items"]
    first_window = datetime.fromisoformat(items[0]["window_start"])
    cal = await _get(
        env,
        "/calendar",
        view="month",
        date=first_window.date().isoformat(),
        tz="America/New_York",
        campaign_id=campaign_id,
    )
    kinds = {i["kind"] for i in cal["items"]}
    assert kinds == {"PLAN_ITEM"}
    assert cal["range_start"].endswith(("-04:00", "-05:00"))

    assets = await approve_all(env, campaign_id)
    accounts = await platform_accounts(env, "LINKEDIN")
    asset = next(a for a in assets if a["platform"] == "LINKEDIN")
    when = datetime.now(UTC) + timedelta(days=2)
    resp = await env.client.post(
        f"/api/v1/approvals/{asset['id']}/actions",
        headers=env.headers,
        json={
            "action": "SCHEDULE",
            "content_version_id": asset["approved_version_id"],
            "platform_account_id": accounts["LINKEDIN"],
            "scheduled_at": when.isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    week = await _get(
        env,
        "/calendar",
        view="week",
        date=when.date().isoformat(),
        tz="UTC",
        campaign_id=campaign_id,
    )
    jobs = [i for i in week["items"] if i["kind"] == "PUBLICATION_JOB"]
    assert [j["content_asset_id"] for j in jobs] == [asset["id"]]
    assert jobs[0]["content_asset_status"] == "SCHEDULED"
    assert jobs[0]["publication_job_status"] == "PENDING"
    # The scheduled asset's plan item is no longer an unscheduled card.
    month = await _get(
        env,
        "/calendar",
        view="month",
        date=first_window.date().isoformat(),
        tz="UTC",
        campaign_id=campaign_id,
    )
    plan_cards = [i for i in month["items"] if i["kind"] == "PLAN_ITEM"]
    assert asset["id"] not in {i["content_asset_id"] for i in plan_cards}

    bad = await env.client.get(
        "/api/v1/calendar",
        headers=env.headers,
        params={"view": "week", "date": "2026-10-05", "tz": "Mars/Olympus"},
    )
    assert bad.status_code == 422


# ------------------------------------------------------------------ scheduled pulls + IK
async def test_scheduled_pulls_are_throttled_and_skip_quiet_campaigns(env: Env) -> None:
    ready = await _published(env)
    queue = RecordingJobQueue()
    now = ready.due + timedelta(minutes=5)
    config = AnalyticsPullConfig(interval_minutes=60, window_days=7, max_campaigns_per_tick=500)
    async with get_sessionmaker()() as session:
        first = await schedule_due_pulls(session, queue, now=now, config=config)
        again = await schedule_due_pulls(session, queue, now=now, config=config)
        later_window = await schedule_due_pulls(
            session,
            queue,
            now=now + timedelta(days=30),
            config=config,
        )
        disabled = await schedule_due_pulls(
            session, queue, now=now, config=AnalyticsPullConfig(enabled=False)
        )
    assert first, "a campaign with a fresh post must be pulled"
    jobs = [await _get(env, f"/jobs/{job_id}") for job_id in first]
    mine = [j for j in jobs if j["subject_id"] == ready.campaign_id]
    assert len(mine) == 1 and mine[0]["kind"] == JobKind.ANALYTICS_PULL.value
    async with get_sessionmaker()() as session:
        row = await session.get(Job, uuid.UUID(mine[0]["id"]))
        assert row is not None and row.requested_by_type is ActorType.SCHEDULER
    assert ready.campaign_id not in {str(j) for j in again}  # queued pull still open
    assert not {str(j) for j in later_window} & {ready.campaign_id}
    assert disabled == []


async def test_snapshot_ingest_requires_an_idempotency_key(env: Env) -> None:
    resp = await env.client.post(
        "/api/v1/analytics/snapshots",
        headers={**env.headers, SKIP_IK: "1"},
        json={"items": []},
    )
    assert resp.status_code in (400, 422)
    ready = await _published(env)
    body = {"items": [_snap(ready.publications[0], ready.due + timedelta(hours=4), impressions=10)]}
    missing = await env.client.post(
        "/api/v1/analytics/snapshots", headers={**env.headers, SKIP_IK: "1"}, json=body
    )
    assert missing.status_code == 400
    assert missing.json()["details"]["reason"] == "IDEMPOTENCY_KEY_REQUIRED"
    key = {**env.headers, "Idempotency-Key": "phase8-ingest-1"}
    one = await env.client.post("/api/v1/analytics/snapshots", headers=key, json=body)
    two = await env.client.post("/api/v1/analytics/snapshots", headers=key, json=body)
    assert one.status_code == two.status_code == 201
    assert two.headers.get("Idempotent-Replayed") == "true"
    assert one.json() == two.json()
