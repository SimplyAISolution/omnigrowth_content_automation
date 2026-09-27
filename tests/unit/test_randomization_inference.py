"""Randomization inference (analytics/randomization.py): exactness, decision/interval
agreement, and — the item-4 check — that the planned sample size delivers the planned power
and error rate when posts from the same account are DEPENDENT (account effects, AR(1)
serial correlation, follower-growth trend), using the real allocation routine."""

from __future__ import annotations

import itertools
import math
import random
import uuid

import pytest

from omnigrowth.analytics.experiment_stats import required_sample
from omnigrowth.analytics.randomization import randomization_test
from omnigrowth.services.experiments import _run_sizes, allocate


def test_monte_carlo_p_matches_exact_enumeration() -> None:
    values = [3.1, 2.4, 5.0, 4.2, 6.3, 5.9, 2.2, 4.8]
    treated = [0, 0, 1, 0, 1, 1, 0, 1]
    observed = (
        sum(v for v, t in zip(values, treated, strict=True) if t) / 4
        - sum(v for v, t in zip(values, treated, strict=True) if not t) / 4
    )
    extreme = 0
    combos = list(itertools.combinations(range(8), 4))
    for chosen in combos:  # all 70 allocations of 4 treated among 8 (one block)
        diff = (
            sum(values[i] for i in chosen) / 4
            - sum(values[i] for i in range(8) if i not in chosen) / 4
        )
        extreme += abs(diff) >= abs(observed) - 1e-12
    exact = extreme / len(combos)
    result = randomization_test(
        values, treated, [0] * 8, [0] * 8, alpha=0.05, permutations=20_000, seed=3
    )
    assert result is not None
    assert result.estimate == pytest.approx(observed)
    assert result.p_value == pytest.approx(exact, abs=0.01)


def test_significance_and_interval_always_agree() -> None:
    rng = random.Random(5)  # noqa: S311 - test data
    for shift in (0.0, 0.3, 0.6, 1.0, 1.5):
        values, treated = [], []
        for i in range(40):
            t = i % 2
            values.append(rng.gauss(0, 1) + shift * t)
            treated.append(t)
        blocks = [i // 2 for i in range(40)]
        result = randomization_test(
            values, treated, [0] * 40, blocks, alpha=0.05, permutations=1_999, seed=1
        )
        assert result is not None
        assert (result.p_value <= 0.05) == (result.ci_low > 0 or result.ci_high < 0)
        assert result.ci_low <= result.estimate <= result.ci_high


def test_accounts_without_both_arms_are_left_out() -> None:
    assert randomization_test([1, 2, 3], [1, 1, 1], [0, 0, 0], [0, 0, 0], alpha=0.05) is None


# ------------------------------------------------------------------ dependence study
def _simulate(effect: float, *, n_per_variant: int, rho: float, seed: int) -> bool:
    """Two accounts; outcome = account level + growth trend + AR(1) noise + effect.
    Units are allocated with the production routine (blocked, V or 2V runs)."""
    rng = random.Random(seed)  # noqa: S311 - simulation
    variants = [uuid.UUID(int=1), uuid.UUID(int=2)]
    values: list[float] = []
    treated: list[int] = []
    accounts: list[int] = []
    blocks: list[int] = []
    for account in (0, 1):
        level = rng.gauss(0, 3)  # big account-to-account difference
        noise = 0.0
        per_account = n_per_variant  # 2 accounts x n_per_variant = 2 n_per_variant units
        sizes = _run_sizes(seed, f"acct{account}", 2)
        run, start, size = 0, 0, next(sizes)
        for seq in range(per_account):
            while seq >= start + size:
                start += size
                size = next(sizes)
                run += 1
            arm = allocate(seed, f"acct{account}", seq, variants) == variants[1]
            noise = rho * noise + math.sqrt(1 - rho * rho) * rng.gauss(0, 1)
            trend = 0.02 * seq  # follower growth over time
            values.append(level + trend + noise + (effect if arm else 0.0))
            treated.append(int(arm))
            accounts.append(account)
            blocks.append(account * 10_000 + run)
    result = randomization_test(
        values, treated, accounts, blocks, alpha=0.05, permutations=499, seed=seed
    )
    assert result is not None
    return result.p_value <= 0.05


@pytest.mark.parametrize("rho", [0.0, 0.5])
def test_planned_sample_gives_planned_power_and_error_under_dependence(rho: float) -> None:
    effect = 0.6  # minimum meaningful effect in SD units of the per-post noise
    plan = required_sample(sigma=1.0, delta=effect, alpha=0.05, power=0.8, comparisons=1, floor=30)
    n = plan.required_per_variant  # 44 per variant from the normal-approximation formula
    reps = 200
    power = (
        sum(_simulate(effect, n_per_variant=n, rho=rho, seed=1000 + r) for r in range(reps)) / reps
    )
    type_one = (
        sum(_simulate(0.0, n_per_variant=n, rho=rho, seed=5000 + r) for r in range(reps)) / reps
    )
    # Monte Carlo error with 200 reps: power +/- ~0.03, alpha +/- ~0.015 (1 SE).
    assert power >= 0.72, power
    assert type_one <= 0.09, type_one


def test_units_per_comparison_are_capped() -> None:
    from omnigrowth.analytics.randomization import MAX_UNITS

    n = MAX_UNITS + 1
    with pytest.raises(ValueError, match="capped"):
        randomization_test([0.0] * n, [i % 2 for i in range(n)], [0] * n, [0] * n, alpha=0.05)


def test_legacy_experiment_without_timeline_falls_back_to_end_at() -> None:
    """Regression (review B2): rows created before the explicit timeline never crash."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from omnigrowth.services.experiments import _allocation_end

    end = datetime(2026, 10, 1, tzinfo=UTC)
    legacy = SimpleNamespace(design={}, end_at=end)
    assert _allocation_end(legacy) == end  # type: ignore[arg-type]
    new = SimpleNamespace(
        design={"timeline": {"allocation_end_at": "2026-09-30T00:00:00+00:00"}}, end_at=end
    )
    assert _allocation_end(new) == datetime(2026, 9, 30, tzinfo=UTC)  # type: ignore[arg-type]
