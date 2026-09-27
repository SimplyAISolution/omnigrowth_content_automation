"""Statistics for experiments (spec §18, SPEC_AMENDMENTS A14). Pure functions, stdlib only.

Method (documented in docs/EXPERIMENTS.md):
* Unit of analysis = one published post. Its value is the primary metric read from ONE
  snapshot at a fixed age; impressions and repeated pulls are never extra samples.
* Two-sided Welch t-test of each treatment against the control; posts are blocked by
  platform account (a stratified difference in means with Welch-Satterthwaite df), which
  reduces to the plain Welch test when there is one account.
* Several treatments: Bonferroni-adjusted p-values and matching Bonferroni-simultaneous
  confidence intervals (family-wise error at alpha; a significant result always has an
  interval that excludes 0).
* Planned sample size: n per variant = ceil(2 (z_{1-alpha/(2m)} + z_{power})^2 sigma^2 /
  delta^2), then the larger of that and the floor (30).

A confidence interval at level 0.95 is a statement about the procedure (95 % of such
intervals cover the true difference), NOT a 95 % probability that a variant is better.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist, fmean, variance

_NORMAL = NormalDist()


# ------------------------------------------------------------------ Student t distribution
def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            return h
    return h


def incomplete_beta(x: float, a: float, b: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    if math.isinf(t):
        return 1.0 if t > 0 else 0.0
    tail = 0.5 * incomplete_beta(df / (df + t * t), df / 2.0, 0.5)
    return 1.0 - tail if t > 0 else tail


def t_ppf(p: float, df: float) -> float:
    """Quantile of Student's t (bisection on the CDF; |error| < 1e-10)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    if p == 0.5:
        return 0.0
    lo, hi = -1e4, 1e4
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return (lo + hi) / 2.0


def z_ppf(p: float) -> float:
    return _NORMAL.inv_cdf(p)


# ------------------------------------------------------------------ comparisons
@dataclass(frozen=True, slots=True)
class Comparison:
    """Treatment minus control."""

    difference: float
    std_error: float
    df: float
    t: float
    p_value: float
    ci_low: float
    ci_high: float
    ci_level: float
    relative_lift: float | None  # difference / control mean (None when control mean is 0)
    hedges_g: float | None  # standardised effect size (pooled within-block SD)
    blocks_used: int
    degenerate: bool = False  # zero variance in both arms


@dataclass(frozen=True, slots=True)
class Arm:
    values: tuple[float, ...]

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def mean(self) -> float:
        return fmean(self.values)

    @property
    def var(self) -> float:
        return variance(self.values) if self.n > 1 else 0.0


def _p_two_sided(t: float, df: float) -> float:
    return max(0.0, min(1.0, 2.0 * (1.0 - t_cdf(abs(t), df))))


def compare(
    control: Mapping[str, Sequence[float]],
    treatment: Mapping[str, Sequence[float]],
    *,
    ci_level: float,
) -> Comparison | None:
    """Blocked (stratified) Welch comparison. Keys are blocks (platform accounts); a block
    is used only when both arms have >= 2 units in it. Returns None when no block is usable.

    Weights: w_b = n_c n_t / (n_c + n_t) (the harmonic sample size of the block, the
    CMH-style weighting). Var = sum(w_b^2 (s_c^2/n_c + s_t^2/n_t)) / W^2; df by
    Welch-Satterthwaite over every (block, arm) variance component.
    """
    blocks = [
        (Arm(tuple(control[b])), Arm(tuple(treatment[b])))
        for b in sorted(set(control) & set(treatment))
        if len(control[b]) >= 2 and len(treatment[b]) >= 2
    ]
    if not blocks:
        return None
    weights = [c.n * t.n / (c.n + t.n) for c, t in blocks]
    total = math.fsum(weights)
    difference = (
        math.fsum(w * (t.mean - c.mean) for w, (c, t) in zip(weights, blocks, strict=True)) / total
    )
    components = [
        (w / total) ** 2 * arm.var / arm.n
        for w, (c, t) in zip(weights, blocks, strict=True)
        for arm in (c, t)
    ]
    var = math.fsum(components)
    dof_terms = [
        ((w / total) ** 2 * arm.var / arm.n) ** 2 / (arm.n - 1)
        for w, (c, t) in zip(weights, blocks, strict=True)
        for arm in (c, t)
    ]
    # Relative lift against the control mean under the same block weights as the difference.
    control_mean = math.fsum(w * c.mean for w, (c, _) in zip(weights, blocks, strict=True)) / total
    lift = difference / control_mean if control_mean != 0 else None
    pooled_num = math.fsum((c.n - 1) * c.var + (t.n - 1) * t.var for c, t in blocks)
    pooled_den = sum(c.n + t.n - 2 for c, t in blocks)
    pooled_sd = math.sqrt(pooled_num / pooled_den) if pooled_den > 0 else 0.0
    n_total = sum(c.n + t.n for c, t in blocks)
    if var == 0.0:
        same = difference == 0.0
        return Comparison(
            difference=difference,
            std_error=0.0,
            df=float(n_total - 2),
            t=0.0 if same else math.copysign(math.inf, difference),
            p_value=1.0 if same else 0.0,
            ci_low=difference,
            ci_high=difference,
            ci_level=ci_level,
            relative_lift=lift,
            hedges_g=None,
            blocks_used=len(blocks),
            degenerate=True,
        )
    se = math.sqrt(var)
    df = var**2 / math.fsum(dof_terms) if math.fsum(dof_terms) > 0 else float(n_total - 2)
    t_stat = difference / se
    critical = t_ppf(1.0 - (1.0 - ci_level) / 2.0, df)
    g = None
    if pooled_sd > 0 and pooled_den > 0:
        # Small-sample correction J = 1 - 3 / (4 df - 1), df = pooled within-block df.
        correction = 1.0 - 3.0 / (4.0 * pooled_den - 1.0)
        g = correction * difference / pooled_sd
    return Comparison(
        difference=difference,
        std_error=se,
        df=df,
        t=t_stat,
        p_value=_p_two_sided(t_stat, df),
        ci_low=difference - critical * se,
        ci_high=difference + critical * se,
        ci_level=ci_level,
        relative_lift=lift,
        hedges_g=g,
        blocks_used=len(blocks),
    )


def bonferroni(p_values: Sequence[float]) -> list[float]:
    """Bonferroni-adjusted p-values (min(1, m p)). Used for decisions because it matches
    the simultaneous intervals exactly: an interval at 1 - alpha/m excludes 0 if and only if
    the adjusted p-value is below alpha (Holm would not have matching intervals)."""
    m = len(p_values)
    return [min(1.0, m * p) for p in p_values]


# ------------------------------------------------------------------ planning
@dataclass(frozen=True, slots=True)
class SamplePlan:
    required_per_variant: int
    power_based: int | None
    floor: int
    basis: str  # "POWER" | "FLOOR" (power requirement below floor) | "FLOOR_ONLY_NO_BASELINE"
    alpha_per_comparison: float
    sigma: float | None
    delta: float | None


def required_sample(
    *,
    sigma: float | None,
    delta: float | None,
    alpha: float,
    power: float,
    comparisons: int,
    floor: int,
) -> SamplePlan:
    """Per-variant units for a two-sided test of each treatment against control.

    alpha is split across ``comparisons`` (Bonferroni) for planning,
    matching the Bonferroni analysis. Without a baseline (sigma) or a positive
    minimum meaningful difference (delta) no power calculation is possible and the floor
    applies — reported as such, never as "powered".
    """
    alpha_each = alpha / max(comparisons, 1)
    if sigma is None or delta is None or delta <= 0 or sigma <= 0:
        return SamplePlan(floor, None, floor, "FLOOR_ONLY_NO_BASELINE", alpha_each, sigma, delta)
    z = z_ppf(1.0 - alpha_each / 2.0) + z_ppf(power)
    n = math.ceil(2.0 * z * z * sigma * sigma / (delta * delta))
    basis = "POWER" if n >= floor else "FLOOR"
    return SamplePlan(max(n, floor), n, floor, basis, alpha_each, sigma, delta)
