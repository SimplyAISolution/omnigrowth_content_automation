"""Integration: Phase 9 gate — experiments (spec §18, SPEC_AMENDMENTS A14).

Gate: no winner below the sample threshold; recommendations without evidence ids are
rejected. Also: the unit is a post (impressions and repeated pulls are not samples),
fixed endpoint (no peeking), simulated evidence never selects a winner, observational
comparisons never declare one, blocked randomization, power-based sample plan, and the
statistics in the stored result equal an independent recomputation.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from omnigrowth.analytics.experiment_stats import compare
from omnigrowth.core.enums import (
    ExperimentStatus,
    MetricSource,
    Platform,
    PublishMode,
    RecommendationStatus,
    UserRole,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.models import AgentRun, Experiment, Recommendation
from omnigrowth.services import experiments as experiment_service
from omnigrowth.storage.db import get_sessionmaker
from tests.integration.campaign_support import Env, generated_campaign
from tests.integration.experiment_support import make_assets, publish, snapshot
from tests.integration.helpers import auth_headers

pytestmark = pytest.mark.integration

OBS_H = 12
TOL_H = 3  # tolerance <= observation / 4, and 2 x tolerance >= 6 h pull interval
LAG_H = 6  # analytics lag >= pull interval
ALLOC_H = 2  # allocation period after creation


@pytest.fixture
def env(campaign_env: Env) -> Env:
    return campaign_env


async def _account(env: Env, handle: str) -> str:
    owner = await auth_headers(env.client, UserRole.OWNER)
    resp = await env.client.post(
        "/api/v1/platform-accounts",
        headers=owner,
        json={
            "brand_id": env.brand_id,
            "platform": "LINKEDIN",
            "account_type": "ORGANIZATION",
            "handle": handle,
            "timezone": "UTC",
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


VARIANTS = [
    {"label": "A", "description": "statement hook (control)"},
    {"label": "B", "description": "question hook"},
]


def _body(
    env: Env,
    campaign_id: str,
    accounts: list[str],
    variants: list[dict[str, str]] | None = None,
    **design: Any,
) -> dict[str, Any]:
    return {
        "brand_id": env.brand_id,
        "campaign_id": campaign_id,
        "name": "Question hook vs statement hook",
        "hypothesis": "A question hook raises the click rate of LinkedIn posts.",
        "variable": "HOOK",
        "metric": "click_rate",
        "allocation_end_at": (datetime.now(UTC) + timedelta(hours=ALLOC_H)).isoformat(),
        "design": {
            "platform": "LINKEDIN",
            "platform_account_ids": accounts,
            "observation_hours": OBS_H,
            "age_tolerance_hours": TOL_H,
            "analytics_lag_hours": LAG_H,
            "minimum_effect": {"kind": "ABSOLUTE", "value": 0.005},
            "single_variable_attested": True,
            **design,
        },
        "variants": variants or VARIANTS,
    }


class Run:
    experiment_id: uuid.UUID
    campaign_id: str
    accounts: list[str]
    units: list[dict[str, Any]]
    published: dict[str, uuid.UUID]  # asset -> publication
    end_at: datetime


async def _setup(
    env: Env,
    *,
    per_variant: int,
    assignment: str = "RANDOMIZED",
    accounts: int = 1,
    variants: list[dict[str, str]] | None = None,
) -> Run:
    run = Run()
    run.campaign_id = await generated_campaign(env)
    run.accounts = [await _account(env, f"exp-{uuid.uuid4().hex[:8]}") for _ in range(accounts)]
    resp = await env.client.post(
        "/api/v1/experiments",
        headers=env.headers,
        json=_body(env, run.campaign_id, run.accounts, variants, assignment=assignment),
    )
    assert resp.status_code == 201, resp.text
    run.experiment_id = uuid.UUID(resp.json()["id"])
    async with get_sessionmaker()() as session:
        assets = await make_assets(
            session,
            brand_id=uuid.UUID(env.brand_id),
            campaign_id=uuid.UUID(run.campaign_id),
            platform=Platform.LINKEDIN,
            n=len(variants or VARIANTS) * per_variant,
        )
    body: list[dict[str, Any]] = []
    for i, asset in enumerate(assets):
        unit: dict[str, Any] = {
            "content_asset_id": str(asset),
            "platform_account_id": run.accounts[i % accounts],
        }
        if assignment == "OBSERVATIONAL":
            labels = [v["label"] for v in variants or VARIANTS]
            unit["variant_label"] = labels[i % len(labels)]
        body.append(unit)
    resp = await env.client.post(
        f"/api/v1/experiments/{run.experiment_id}/units", headers=env.headers, json={"units": body}
    )
    assert resp.status_code == 201, resp.text
    run.units = resp.json()
    return run


async def _start_publish_measure(
    env: Env,
    run: Run,
    *,
    source: MetricSource,
    effect: float | dict[str, float],
    seed: int = 11,
    mode: PublishMode = PublishMode.MANUAL,
) -> None:
    """Start, publish every unit, record one snapshot per unit at the design age (plus
    decoy pulls at other ages), move to COLLECTING."""
    resp = await env.client.post(
        f"/api/v1/experiments/{run.experiment_id}/start", headers=env.headers
    )
    assert resp.status_code == 200, resp.text
    run.end_at = datetime.fromisoformat(resp.json()["end_at"])
    rng = random.Random(seed)  # noqa: S311 - simulated metric noise in a test
    run.published = {}
    brand = uuid.UUID(env.brand_id)
    published_at = datetime.now(UTC) + timedelta(minutes=1)  # inside the allocation period
    effects = effect if isinstance(effect, dict) else {"B": effect}
    async with get_sessionmaker()() as session:
        for unit in run.units:
            pub = await publish(
                session,
                brand_id=brand,
                asset_id=uuid.UUID(unit["content_asset_id"]),
                account_id=uuid.UUID(unit["platform_account_id"]),
                platform=Platform.LINKEDIN,
                published_at=published_at,
                mode=mode,
            )
            run.published[unit["content_asset_id"]] = pub
            impressions = rng.randint(800, 5_000)  # impressions vary: not samples
            rate = 0.02 + effects.get(unit["variant_label"], 0.0) + rng.gauss(0, 0.004)
            clicks = max(0, round(rate * impressions))
            await snapshot(
                session,
                brand_id=brand,
                publication_id=pub,
                captured_at=published_at + timedelta(hours=OBS_H),
                source=source,
                impressions=impressions,
                clicks=clicks,
            )
            # Repeated pulls at other ages must not become extra samples.
            for hours in (0.25, OBS_H + TOL_H + 2):
                await snapshot(
                    session,
                    brand_id=brand,
                    publication_id=pub,
                    captured_at=published_at + timedelta(hours=hours),
                    source=source,
                    impressions=impressions * 3,
                    clicks=clicks * 5,
                )
        counts = await experiment_service.sync_experiments(session, now=datetime.now(UTC))
    assert counts["collecting"] >= 1


async def _evaluate(run: Run, *, at: datetime | None = None) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        return await experiment_service.evaluate_experiment(
            session,
            experiment_service.SYSTEM_EXPERIMENTS,
            run.experiment_id,
            now=at or (run.end_at + timedelta(seconds=1)),
        )


async def _results(env: Env, run: Run) -> dict[str, Any]:
    resp = await env.client.get(
        f"/api/v1/experiments/{run.experiment_id}/results", headers=env.headers
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


# ------------------------------------------------------------------ gate
async def test_no_winner_below_the_sample_threshold_even_with_a_huge_effect(env: Env) -> None:
    run = await _setup(env, per_variant=12)
    await _start_publish_measure(env, run, source=MetricSource.MANUAL, effect=0.03)
    outcome = await _evaluate(run)
    assert outcome["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert outcome["failed_guard"] == "insufficient_sample"
    results = await _results(env, run)
    assert results["status"] == "INCONCLUSIVE" and results["winner_variant_id"] is None
    counts = [v["n"] for v in results["real"]["per_variant"]]
    assert sum(counts) == 24 and max(counts) < 30  # blocks of 2 or 4: near, not exact
    assert all(not v["meets_requirement"] for v in results["real"]["per_variant"])
    # The effect estimate is still reported (with its interval), just not as a winner.
    assert results["real"]["comparisons"][0]["difference"] > 0


async def test_recommendation_without_evidence_ids_is_rejected(env: Env) -> None:
    """Phase 9 gate, second half: the schema (min 1 id), the service (labels must resolve,
    see test_e2e_success_scenario) and the database (has_evidence CHECK) all refuse it."""
    from pydantic import ValidationError

    from omnigrowth.agents.optimizer import Recommendation as Proposed

    with pytest.raises(ValidationError):
        Proposed.model_validate(
            {"statement": "Post more carousels", "rationale": "vibes", "evidence": []}
        )
    async with get_sessionmaker()() as session:
        run_id = uuid7()
        session.add(
            AgentRun(
                id=run_id,
                brand_id=uuid.UUID(env.brand_id),
                agent_name="optimizer",
                correlation_id="test",
                input_ref={},
            )
        )
        await session.flush()
        session.add(
            Recommendation(
                id=uuid7(),
                brand_id=uuid.UUID(env.brand_id),
                agent_run_id=run_id,
                statement="Post more carousels",
                evidence_snapshot_ids=[],
                evidence_query={},
                status=RecommendationStatus.PROPOSED,
            )
        )
        with pytest.raises(IntegrityError, match="has_evidence"):
            await session.commit()


# ------------------------------------------------------------------ winners and non-winners
async def test_winner_needs_real_randomized_sufficient_significant_data(env: Env) -> None:
    run = await _setup(env, per_variant=32)
    await _start_publish_measure(env, run, source=MetricSource.MANUAL, effect=0.006)
    outcome = await _evaluate(run)
    assert outcome["verdict"] == "WINNER", outcome
    results = await _results(env, run)
    b = next(v for v in results["real"]["per_variant"] if v["label"] == "B")
    assert results["status"] == "CONCLUDED" and results["winner_variant_id"] == b["variant_id"]
    assert results["confidence_level"] == 0.95
    assert "NOT the probability" in results["interpretation"]
    # n counts posts: one value per post, whatever the impressions or number of pulls.
    counts = [v["n"] for v in results["real"]["per_variant"]]
    # 64 posts, blocked allocation with V or 2V runs: arms differ by at most one run (2).
    assert sum(counts) == 64 and max(counts) - min(counts) <= 2
    assert all(len(v["snapshot_ids"]) == v["n"] for v in results["real"]["per_variant"])
    comparison = results["real"]["comparisons"][0]
    for key in ("difference", "relative_lift", "hedges_g", "ci_low", "ci_high", "p_value_adjusted"):
        assert comparison[key] is not None
    assert comparison["ci_low"] > 0 and comparison["significant"]
    # Independent recomputation from the snapshots the result cites.
    async with get_sessionmaker()() as session:
        from omnigrowth.models import AnalyticsSnapshot

        values: dict[str, list[float]] = {}
        for v in results["real"]["per_variant"]:
            rows = (
                await session.execute(
                    select(AnalyticsSnapshot).where(
                        AnalyticsSnapshot.id.in_([uuid.UUID(s) for s in v["snapshot_ids"]])
                    )
                )
            ).scalars()
            values[v["label"]] = [s.clicks / s.impressions for s in rows]
    expected = compare({"acct": values["A"]}, {"acct": values["B"]}, ci_level=0.95)
    assert expected is not None
    # Same stratified estimator; Welch figures are stored as descriptive only.
    assert comparison["difference"] == pytest.approx(expected.difference)
    assert comparison["welch_descriptive"]["p_value"] == pytest.approx(expected.p_value)
    assert comparison["welch_descriptive"]["ci_low"] == pytest.approx(expected.ci_low)
    # The decision is the randomization test: p from 9,999 re-draws of the allocation,
    # interval by inversion containing the estimate and excluding 0.
    assert comparison["p_value"] == pytest.approx(1 / 10_000)
    assert comparison["ci_low"] > 0 and comparison["ci_low"] <= comparison["difference"]
    assert "randomization test" in comparison["method"]
    assert results["evidence_snapshot_ids"]
    async with get_sessionmaker()() as session:
        experiment = await session.get(Experiment, run.experiment_id)
        assert experiment is not None and experiment.min_sample_size >= 30


async def test_simulated_evidence_never_selects_a_winner(env: Env) -> None:
    run = await _setup(env, per_variant=32)
    await _start_publish_measure(
        env, run, source=MetricSource.FIXTURE, effect=0.02, mode=PublishMode.MOCK
    )
    outcome = await _evaluate(run)
    assert outcome["verdict"] == "NO_REAL_DATA"
    results = await _results(env, run)
    assert results["status"] == "INCONCLUSIVE" and results["winner_variant_id"] is None
    simulated = results["simulated"]
    assert simulated is not None and "never selects a winner" in simulated["note"]
    sim_counts = [v["n"] for v in simulated["per_variant"]]
    assert sum(sim_counts) == 64 and max(sim_counts) - min(sim_counts) <= 2
    assert simulated["comparisons"][0]["significant"]  # huge simulated "effect", ignored
    assert [v["n"] for v in results["real"]["per_variant"]] == [0, 0]
    assert results["evidence_snapshot_ids"] == []


async def test_observational_comparison_never_declares_a_winner(env: Env) -> None:
    run = await _setup(env, per_variant=32, assignment="OBSERVATIONAL")
    assert {u["assigned_by"] for u in run.units} == {"MANUAL"}
    await _start_publish_measure(env, run, source=MetricSource.MANUAL, effect=0.02)
    outcome = await _evaluate(run)
    assert outcome["verdict"] == "OBSERVATIONAL_ASSOCIATION"
    results = await _results(env, run)
    assert results["status"] == "INCONCLUSIVE" and results["winner_variant_id"] is None
    async with get_sessionmaker()() as session:
        experiment = await session.get(Experiment, run.experiment_id)
        assert experiment is not None
        experiment.winner_variant_id = uuid.UUID(results["real"]["per_variant"][1]["variant_id"])
        with pytest.raises(IntegrityError):
            await session.commit()  # DB: observational rows can never carry a winner


async def test_fixed_endpoint_no_peeking_and_a_single_evaluation(env: Env) -> None:
    run = await _setup(env, per_variant=32)
    await _start_publish_measure(env, run, source=MetricSource.MANUAL, effect=0.006)
    early = await _evaluate(run, at=run.end_at - timedelta(minutes=1))
    assert early == {"experiment_id": str(run.experiment_id), "no_transition": "WINDOW_OPEN"}
    assert (await _results(env, run))["status"] == "COLLECTING"
    await _evaluate(run)
    with pytest.raises(OmniGrowthError) as info:
        await _evaluate(run)
    assert info.value.code is ErrorCode.INVALID_STATE_TRANSITION


# ------------------------------------------------------------------ design + allocation
async def test_randomization_is_blocked_by_account_and_reproducible(env: Env) -> None:
    run = await _setup(env, per_variant=6, accounts=2)
    detail = (
        await env.client.get(f"/api/v1/experiments/{run.experiment_id}", headers=env.headers)
    ).json()
    seed = detail["design"]["randomization_seed"]
    variant_ids = [uuid.UUID(v["id"]) for v in detail["variants"]]
    by_account: dict[str, list[str]] = {}
    for unit in sorted(run.units, key=lambda u: u["block_seq"]):
        by_account.setdefault(unit["platform_account_id"], []).append(unit["variant_label"])
        assert experiment_service.allocate(
            seed, unit["block_key"], unit["block_seq"], variant_ids
        ) == uuid.UUID(unit["variant_id"])  # every allocation re-derivable from the seed
    for labels in by_account.values():
        # Blocks of 2 or 4: at any point the arms differ by at most one block's excess.
        assert abs(labels.count("A") - labels.count("B")) <= 2


async def test_design_guards(env: Env) -> None:
    campaign_id = await generated_campaign(env)
    account = await _account(env, f"guard-{uuid.uuid4().hex[:6]}")
    low = _body(env, campaign_id, [account], min_units_per_variant=10)
    resp = await env.client.post("/api/v1/experiments", headers=env.headers, json=low)
    assert resp.status_code == 422  # floor is 30 per variant
    wide = _body(env, campaign_id, [account], observation_hours=24, age_tolerance_hours=72)
    resp = await env.client.post("/api/v1/experiments", headers=env.headers, json=wide)
    assert resp.status_code == 422  # a 3-hour and a 96-hour reading are not equivalent ages
    other_level = {**_body(env, campaign_id, [account]), "confidence_level": 0.8}
    resp = await env.client.post("/api/v1/experiments", headers=env.headers, json=other_level)
    assert resp.status_code == 422  # the confidence level is fixed at 0.95
    timing = {**_body(env, campaign_id, [account]), "variable": "POSTING_TIME"}
    resp = await env.client.post("/api/v1/experiments", headers=env.headers, json=timing)
    assert resp.status_code == 422  # exclusions would depend on the variant (review M5)
    assert resp.json()["details"]["field"] == "variable"
    unattested = _body(env, campaign_id, [account], single_variable_attested=False)
    created = await env.client.post("/api/v1/experiments", headers=env.headers, json=unattested)
    assert created.status_code == 201, created.text
    exp_id = created.json()["id"]
    async with get_sessionmaker()() as session:
        (asset,) = await make_assets(
            session,
            brand_id=uuid.UUID(env.brand_id),
            campaign_id=uuid.UUID(campaign_id),
            platform=Platform.X,
            n=1,
        )
    wrong = await env.client.post(
        f"/api/v1/experiments/{exp_id}/units",
        headers=env.headers,
        json={"units": [{"content_asset_id": str(asset), "platform_account_id": account}]},
    )
    assert wrong.status_code == 422  # platform differs from the design
    start = await env.client.post(f"/api/v1/experiments/{exp_id}/start", headers=env.headers)
    assert start.status_code == 409
    assert start.json()["details"]["failed_guard"] == "single_variable_attested"


async def test_sample_plan_uses_real_baseline_variability_and_the_floor(env: Env) -> None:
    """Twelve earlier REAL posts on the account give sigma; the plan is
    max(30, ceil(2 (z_.975 + z_.8)^2 sigma^2 / delta^2))."""
    import math
    import statistics

    from omnigrowth.analytics.experiment_stats import z_ppf

    campaign_id = await generated_campaign(env)
    account = await _account(env, f"base-{uuid.uuid4().hex[:6]}")
    brand = uuid.UUID(env.brand_id)
    rates = [0.010, 0.030, 0.020, 0.040, 0.015, 0.035, 0.025, 0.005, 0.045, 0.020, 0.030, 0.025]
    async with get_sessionmaker()() as session:
        history = await make_assets(
            session,
            brand_id=brand,
            campaign_id=uuid.UUID(campaign_id),
            platform=Platform.LINKEDIN,
            n=len(rates),
        )
        at = datetime.now(UTC) - timedelta(days=3)
        for asset, rate in zip(history, rates, strict=True):
            pub = await publish(
                session,
                brand_id=brand,
                asset_id=asset,
                account_id=uuid.UUID(account),
                platform=Platform.LINKEDIN,
                published_at=at,
            )
            await snapshot(
                session,
                brand_id=brand,
                publication_id=pub,
                captured_at=at + timedelta(hours=OBS_H),
                source=MetricSource.MANUAL,
                impressions=10_000,
                clicks=round(rate * 10_000),
            )
        units = await make_assets(
            session,
            brand_id=brand,
            campaign_id=uuid.UUID(campaign_id),
            platform=Platform.LINKEDIN,
            n=4,  # >= one full block of 2 or 4, so every variant gets a unit
        )
    body = _body(env, campaign_id, [account])
    exp = (await env.client.post("/api/v1/experiments", headers=env.headers, json=body)).json()
    await env.client.post(
        f"/api/v1/experiments/{exp['id']}/units",
        headers=env.headers,
        json={
            "units": [{"content_asset_id": str(u), "platform_account_id": account} for u in units]
        },
    )
    started = await env.client.post(f"/api/v1/experiments/{exp['id']}/start", headers=env.headers)
    assert started.status_code == 200, started.text
    plan = started.json()["design"]["sample_plan"]
    sigma = statistics.stdev(rates)
    z = z_ppf(0.975) + z_ppf(0.8)
    # delta = 0.005 absolute; default power_inflation 1.25 for within-account dependence
    expected = math.ceil(2 * z * z * sigma**2 / 0.005**2 * 1.25)
    assert plan["baseline_posts"] == len(rates) and plan["baseline_sd"] == pytest.approx(sigma)
    assert plan["power_based"] == expected
    assert plan["power_inflation"] == 1.25
    assert plan["required_units_per_variant"] == max(expected, 30)
    assert started.json()["min_units_per_variant"] == max(expected, 30)
    assert started.json()["status"] == ExperimentStatus.RUNNING.value


async def test_evaluate_endpoint_queues_a_job_that_refuses_to_peek(env: Env) -> None:
    from omnigrowth.workflows.tasks import execute_job

    run = await _setup(env, per_variant=2)
    await _start_publish_measure(env, run, source=MetricSource.MANUAL, effect=0.0)
    resp = await env.client.post(
        f"/api/v1/experiments/{run.experiment_id}/evaluate", headers=env.headers
    )
    assert resp.status_code == 202, resp.text
    job_id = uuid.UUID(resp.json()["id"])
    await execute_job(job_id)  # real clock: end_at is hours away
    job = (await env.client.get(f"/api/v1/jobs/{job_id}", headers=env.headers)).json()
    assert job["status"] == "SUCCEEDED"
    assert job["result"]["no_transition"] == "WINDOW_OPEN"
    assert (await _results(env, run))["status"] == "COLLECTING"


# ------------------------------------------------------------------ review regressions
async def test_manual_numbers_on_a_mock_post_are_not_real_evidence(env: Env) -> None:
    """Regression (reviewer M1): a MOCK publication never reached an audience, so even
    MANUAL-labelled numbers on it are simulated; and the ingest API refuses them."""
    run = await _setup(env, per_variant=32)
    await _start_publish_measure(
        env, run, source=MetricSource.MANUAL, effect=0.02, mode=PublishMode.MOCK
    )
    outcome = await _evaluate(run)
    assert outcome["verdict"] == "NO_REAL_DATA"
    results = await _results(env, run)
    assert results["winner_variant_id"] is None and results["evidence_snapshot_ids"] == []
    excluded = [v["excluded"] for v in results["real"]["per_variant"]]
    assert sum(e.get("MOCK_PUBLICATION", 0) for e in excluded) == 64  # every unit
    assert results["simulated"] is not None
    pub = next(iter(run.published.values()))
    resp = await env.client.post(
        "/api/v1/analytics/snapshots",
        headers=env.headers,
        json={
            "items": [
                {
                    "publication_id": str(pub),
                    "captured_at": datetime.now(UTC).isoformat(),
                    "source": "MANUAL",
                    "impressions": 10,
                }
            ]
        },
    )
    assert resp.status_code == 422 and resp.json()["details"]["field"] == "source"


THREE = [
    {"label": "A", "description": "statement hook (control)"},
    {"label": "B", "description": "question hook"},
    {"label": "C", "description": "number-led hook"},
]


async def test_two_treatments_beating_control_need_a_pairwise_difference(env: Env) -> None:
    """Regression (reviewer M4): B and C both beat A by a similar margin; neither is
    declared the winner because they are not distinguishable from each other."""
    run = await _setup(env, per_variant=32, variants=THREE)
    await _start_publish_measure(
        env, run, source=MetricSource.MANUAL, effect={"B": 0.012, "C": 0.0125}
    )
    outcome = await _evaluate(run)
    assert outcome["verdict"] == "TREATMENTS_BEAT_CONTROL_UNRANKED", outcome
    results = await _results(env, run)
    assert results["winner_variant_id"] is None
    comparisons = results["real"]["comparisons"]
    assert all(c["significant"] and c["favourable"] for c in comparisons)
    assert all(c["adjustment"] == "Bonferroni x2" for c in comparisons)
    assert results["real"]["pairwise_between_winners"]


async def test_a_clearly_best_treatment_wins_among_several(env: Env) -> None:
    run = await _setup(env, per_variant=32, variants=THREE)
    await _start_publish_measure(
        env, run, source=MetricSource.MANUAL, effect={"B": 0.006, "C": 0.02}
    )
    outcome = await _evaluate(run)
    assert outcome["verdict"] == "WINNER", outcome
    results = await _results(env, run)
    c = next(v for v in results["real"]["per_variant"] if v["label"] == "C")
    assert results["winner_variant_id"] == c["variant_id"]
    for comparison in results["real"]["comparisons"]:
        # Decisions and intervals agree: significant <=> the simultaneous interval excludes 0.
        excludes_zero = comparison["ci_low"] > 0 or comparison["ci_high"] < 0
        assert comparison["significant"] == excludes_zero
        assert comparison["ci_level"] == pytest.approx(1 - 0.05 / 2)


# ------------------------------------------------------------------ item 5: frozen timing
async def test_timeline_is_frozen_and_evaluation_waits_for_the_last_window(env: Env) -> None:
    run = await _setup(env, per_variant=32)
    detail = (
        await env.client.get(f"/api/v1/experiments/{run.experiment_id}", headers=env.headers)
    ).json()
    timeline = detail["design"]["timeline"]
    alloc_end = datetime.fromisoformat(timeline["allocation_end_at"])
    cutoff = datetime.fromisoformat(timeline["collection_cutoff_at"])
    assert cutoff - alloc_end == timedelta(hours=OBS_H + TOL_H + LAG_H)
    assert datetime.fromisoformat(detail["end_at"]) == cutoff  # evaluation at the cutoff
    await _start_publish_measure(env, run, source=MetricSource.MANUAL, effect=0.006)
    # A post published at the very end of the allocation period is still measured.
    async with get_sessionmaker()() as session:
        from sqlalchemy import update

        from omnigrowth.models import Publication

        late_asset = run.units[0]["content_asset_id"]
        pub_id = run.published[late_asset]
        await session.execute(
            update(Publication)
            .where(Publication.id == pub_id)
            .values(published_at=alloc_end - timedelta(seconds=1))
        )
        await snapshot(
            session,
            brand_id=uuid.UUID(env.brand_id),
            publication_id=pub_id,
            captured_at=alloc_end + timedelta(hours=OBS_H),
            source=MetricSource.MANUAL,
            impressions=2_000,
            clicks=40,
        )
        await session.commit()
    before_cutoff = await _evaluate(run, at=cutoff - timedelta(seconds=1))
    assert before_cutoff["no_transition"] == "WINDOW_OPEN"
    outcome = await _evaluate(run, at=cutoff)
    results = await _results(env, run)
    excluded = [v["excluded"] for v in results["real"]["per_variant"]]
    assert not any("NOT_YET_AT_AGE" in e for e in excluded), (outcome, excluded)
    assert sum(v["n"] for v in results["real"]["per_variant"]) == 64


async def test_posts_outside_the_allocation_period_are_excluded_and_labelled(env: Env) -> None:
    run = await _setup(env, per_variant=32)
    await _start_publish_measure(env, run, source=MetricSource.MANUAL, effect=0.006)
    alloc_end = datetime.fromisoformat(
        (
            await env.client.get(f"/api/v1/experiments/{run.experiment_id}", headers=env.headers)
        ).json()["design"]["timeline"]["allocation_end_at"]
    )
    async with get_sessionmaker()() as session:
        from sqlalchemy import update

        from omnigrowth.models import Publication

        ids = list(run.published.values())
        await session.execute(
            update(Publication)
            .where(Publication.id == ids[0])
            .values(published_at=alloc_end + timedelta(hours=1))
        )
        await session.execute(
            update(Publication)
            .where(Publication.id == ids[1])
            .values(published_at=datetime.now(UTC) - timedelta(days=2))
        )
        await session.commit()
    await _evaluate(run)
    results = await _results(env, run)
    excluded: dict[str, int] = {}
    for v in results["real"]["per_variant"]:
        for reason, n in v["excluded"].items():
            excluded[reason] = excluded.get(reason, 0) + n
    assert excluded.get("PUBLISHED_AFTER_ALLOCATION_END") == 1
    assert excluded.get("PUBLISHED_BEFORE_START") == 1


async def test_no_units_after_allocation_end_and_sparse_pulls_block_the_start(env: Env) -> None:
    campaign_id = await generated_campaign(env)
    account = await _account(env, f"tl-{uuid.uuid4().hex[:6]}")
    sparse = _body(env, campaign_id, [account], observation_hours=4, age_tolerance_hours=1)
    created = await env.client.post("/api/v1/experiments", headers=env.headers, json=sparse)
    assert created.status_code == 201, created.text
    exp_id = created.json()["id"]
    async with get_sessionmaker()() as session:
        assets = await make_assets(
            session,
            brand_id=uuid.UUID(env.brand_id),
            campaign_id=uuid.UUID(campaign_id),
            platform=Platform.LINKEDIN,
            n=4,
        )
    await env.client.post(
        f"/api/v1/experiments/{exp_id}/units",
        headers=env.headers,
        json={
            "units": [{"content_asset_id": str(a), "platform_account_id": account} for a in assets]
        },
    )
    start = await env.client.post(f"/api/v1/experiments/{exp_id}/start", headers=env.headers)
    assert start.status_code == 409
    assert start.json()["details"]["failed_guard"] == "pulls_cover_measurement_window"

    past = {
        **_body(env, campaign_id, [account]),
        "allocation_end_at": datetime.now(UTC).isoformat(),
    }
    closed = await env.client.post("/api/v1/experiments", headers=env.headers, json=past)
    assert closed.status_code == 201
    late = await env.client.post(
        f"/api/v1/experiments/{closed.json()['id']}/units",
        headers=env.headers,
        json={"units": [{"content_asset_id": str(assets[0]), "platform_account_id": account}]},
    )
    assert late.status_code == 409
    assert late.json()["details"]["failed_guard"] == "before_allocation_end"
