"""Experiment statistics against hand computation, published tables and an independent
numerical integration (no SciPy in the project, so the t distribution is cross-checked)."""

from __future__ import annotations

import math

import pytest

from omnigrowth.analytics.experiment_stats import (
    bonferroni,
    compare,
    incomplete_beta,
    required_sample,
    t_cdf,
    t_ppf,
)


def _t_pdf(x: float, df: float) -> float:
    return math.exp(
        math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi)
    ) * (1 + x * x / df) ** (-(df + 1) / 2)


def _cdf_by_simpson(t: float, df: float, steps: int = 20_000) -> float:
    """Independent check: 0.5 + integral_0^t pdf (composite Simpson)."""
    h = t / steps
    total = _t_pdf(0, df) + _t_pdf(t, df)
    total += sum((4 if i % 2 else 2) * _t_pdf(i * h, df) for i in range(1, steps))
    return 0.5 + total * h / 3


# ------------------------------------------------------------------ distribution
@pytest.mark.parametrize(
    ("df", "quantile"),
    [(1, 12.7062047), (5, 2.5705818), (10, 2.2281389), (30, 2.0422725), (120, 1.9799304)],
)
def test_t_quantiles_match_published_tables(df: int, quantile: float) -> None:
    assert t_ppf(0.975, df) == pytest.approx(quantile, abs=1e-6)
    assert t_cdf(quantile, df) == pytest.approx(0.975, abs=1e-7)  # table rounded to 7 dp


@pytest.mark.parametrize(("t", "df"), [(0.5, 3), (1.897367, 5.88235), (2.0, 5), (3.1, 12.5)])
def test_t_cdf_matches_numerical_integration(t: float, df: float) -> None:
    assert t_cdf(t, df) == pytest.approx(_cdf_by_simpson(t, df), abs=1e-9)
    assert t_cdf(-t, df) == pytest.approx(1 - t_cdf(t, df), abs=1e-12)


def test_incomplete_beta_edges_and_symmetry() -> None:
    assert incomplete_beta(0.0, 2, 3) == 0.0 and incomplete_beta(1.0, 2, 3) == 1.0
    # I_x(a, b) = 1 - I_{1-x}(b, a); I_0.5(a, a) = 0.5
    assert incomplete_beta(0.3, 2, 5) == pytest.approx(1 - incomplete_beta(0.7, 5, 2))
    assert incomplete_beta(0.5, 4, 4) == pytest.approx(0.5)
    # I_x(1, 1) = x ; I_x(a, 1) = x^a
    assert incomplete_beta(0.37, 1, 1) == pytest.approx(0.37)
    assert incomplete_beta(0.6, 3, 1) == pytest.approx(0.216)


# ------------------------------------------------------------------ Welch (hand computed)
def test_single_block_welch_matches_hand_computation() -> None:
    control = {"acct": [1.0, 2.0, 3.0, 4.0, 5.0]}  # mean 3, s^2 2.5
    treatment = {"acct": [2.0, 4.0, 6.0, 8.0, 10.0]}  # mean 6, s^2 10
    c = compare(control, treatment, ci_level=0.95)
    assert c is not None
    # diff 3; se = sqrt(2.5/5 + 10/5) = sqrt(2.5) = 1.5811388
    assert c.difference == pytest.approx(3.0)
    assert c.std_error == pytest.approx(math.sqrt(2.5))
    # df = 2.5^2 / ((0.5^2)/4 + (2^2)/4) = 6.25 / 1.0625 = 5.8823529
    assert c.df == pytest.approx(6.25 / 1.0625)
    assert c.t == pytest.approx(3 / math.sqrt(2.5))  # 1.8973666
    # p = 2 (1 - F(1.8973666; 5.88235)), F from independent integration
    assert c.p_value == pytest.approx(2 * (1 - _cdf_by_simpson(3 / math.sqrt(2.5), 6.25 / 1.0625)))
    half = t_ppf(0.975, 6.25 / 1.0625) * math.sqrt(2.5)
    assert (c.ci_low, c.ci_high) == pytest.approx((3 - half, 3 + half))
    assert c.relative_lift == pytest.approx(1.0)  # 3 / 3
    # Hedges g: pooled s^2 = (4*2.5 + 4*10)/8 = 6.25 -> sd 2.5; d = 1.2; J = 1 - 3/31
    assert c.hedges_g == pytest.approx(1.2 * (1 - 3 / 31))
    assert c.blocks_used == 1


def test_blocking_by_account_removes_the_between_account_level() -> None:
    # Account B runs 100x the level of account A; the treatment adds +1 in both.
    control = {"A": [1.0, 2.0, 3.0], "B": [100.0, 101.0, 102.0]}
    treatment = {"A": [2.0, 3.0, 4.0], "B": [101.0, 102.0, 103.0]}
    c = compare(control, treatment, ci_level=0.95)
    assert c is not None and c.blocks_used == 2
    assert c.difference == pytest.approx(1.0)
    # Within-block variance is 1 per arm: var = sum (w/W)^2 (1/3 + 1/3), w equal -> 1/3
    assert c.std_error == pytest.approx(math.sqrt(2 * 0.25 * (2 / 3)))


def test_block_without_both_arms_is_not_used() -> None:
    assert compare({"A": [1.0, 2.0]}, {"B": [3.0, 4.0]}, ci_level=0.95) is None
    assert compare({"A": [1.0]}, {"A": [3.0, 4.0]}, ci_level=0.95) is None


def test_identical_values_are_degenerate_not_a_crash() -> None:
    c = compare({"A": [5.0, 5.0, 5.0]}, {"A": [5.0, 5.0]}, ci_level=0.95)
    assert c is not None and c.degenerate and c.p_value == 1.0


# ------------------------------------------------------------------ multiplicity + planning
def test_bonferroni_adjustment_hand_computed() -> None:
    # m = 3: 0.01*3 = 0.03 ; 0.04*3 = 0.12 ; 0.03*3 = 0.09 ; capped at 1
    assert bonferroni([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.12, 0.09])
    assert bonferroni([0.2]) == [0.2]
    assert bonferroni([0.6, 0.7]) == [1.0, 1.0]


def test_significance_matches_the_simultaneous_interval() -> None:
    """With m comparisons, adjusted p < alpha exactly when the 1 - alpha/m interval excludes
    0 (why Bonferroni, not Holm, drives decisions)."""
    control = {"a": [1.0, 2.0, 3.0, 4.0, 5.0, 2.5, 3.5]}
    for shift in (0.5, 1.4, 1.8, 2.2, 3.0):
        treatment = {"a": [x + shift for x in control["a"]][::-1]}
        c = compare(control, treatment, ci_level=1 - 0.05 / 3)
        assert c is not None
        significant = min(1.0, 3 * c.p_value) < 0.05
        assert significant == (c.ci_low > 0 or c.ci_high < 0)


def test_allocation_is_balanced_within_every_completed_block() -> None:
    import uuid

    from omnigrowth.services.experiments import _run_sizes, allocate

    variants = [uuid.uuid4() for _ in range(3)]
    sizes = _run_sizes(12345, "acct", 3)
    start, seen_sizes = 0, set()
    for _ in range(60):
        size = next(sizes)
        seen_sizes.add(size)
        run = [allocate(12345, "acct", start + i, variants) for i in range(size)]
        assert sorted(run.count(v) for v in variants) == [size // 3] * 3
        start += size
    assert seen_sizes == {3, 6}  # block sizes vary, so the last slot is not predictable


def test_required_sample_hand_computed() -> None:
    # z_0.975 + z_0.8 = 1.959964 + 0.841621 = 2.801585; 2 z^2 sigma^2 / delta^2
    # = 2 * 7.848879 * 0.01^2 / 0.005^2 = 62.79 -> 63
    plan = required_sample(sigma=0.01, delta=0.005, alpha=0.05, power=0.8, comparisons=1, floor=30)
    assert plan.power_based == 63 and plan.required_per_variant == 63 and plan.basis == "POWER"


def test_floor_applies_when_power_needs_fewer_units() -> None:
    plan = required_sample(sigma=0.01, delta=0.02, alpha=0.05, power=0.8, comparisons=1, floor=30)
    # 2 * 7.848879 * 1e-4 / 4e-4 = 3.92 -> 4, below the floor
    assert plan.power_based == 4 and plan.required_per_variant == 30 and plan.basis == "FLOOR"


def test_multiple_treatments_split_alpha_for_planning() -> None:
    one = required_sample(sigma=1, delta=0.5, alpha=0.05, power=0.8, comparisons=1, floor=2)
    three = required_sample(sigma=1, delta=0.5, alpha=0.05, power=0.8, comparisons=3, floor=2)
    assert three.alpha_per_comparison == pytest.approx(0.05 / 3)
    assert (three.power_based or 0) > (one.power_based or 0)


def test_no_baseline_means_floor_only_never_claimed_powered() -> None:
    plan = required_sample(sigma=None, delta=0.1, alpha=0.05, power=0.8, comparisons=1, floor=30)
    assert plan.required_per_variant == 30 and plan.basis == "FLOOR_ONLY_NO_BASELINE"
    assert plan.power_based is None
