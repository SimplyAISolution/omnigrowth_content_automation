"""Design-based (randomization) inference for experiments (Phase 9 item 4, A14).

Why: posts from one account are not independent (shared audience, follower growth, news
cycles, serial correlation). A Welch test assumes independence within an account, and
comparing posts within accounts does not by itself make them independent. But OmniGrowth
itself randomized the allocation — so the test can be built from the allocation mechanism
instead of from an independence assumption:

* Under the sharp null of no effect for any post, every re-draw of the allocation that the
  design could have produced is equally likely, WHATEVER the correlation between posts.
  Re-drawing = shuffling the labels within each randomization block (an account's run of
  V or 2V consecutive units), which is exactly how the allocation was made.
* Statistic = the account-stratified difference in means: per account the treatment mean
  minus the control mean, combined with weights w = n_c n_t / (n_c + n_t) (the same
  estimator as the descriptive Welch result).
* p-value: Monte Carlo over ``permutations`` re-draws, p = (1 + #{|D*| >= |D|}) / (N + 1).
* Confidence interval: inversion under the constant-shift model — the set of effects d for
  which "effect = d" is not rejected, found by bisection on the SAME re-draws; so a result
  is significant exactly when its interval excludes 0.

Missing values (a post without a snapshot at its age) are dropped; this keeps the test valid
only if missingness is unrelated to the variant (documented in docs/EXPERIMENTS.md).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

DEFAULT_PERMUTATIONS = 9_999
BISECTION_STEPS = 60
MAX_UNITS = 10_000
CHUNK_ROWS = 1_024


@dataclass(frozen=True, slots=True)
class RandomizationResult:
    estimate: float  # account-stratified difference in means (treatment - reference)
    p_value: float
    ci_low: float
    ci_high: float
    ci_level: float
    permutations: int
    accounts: int
    blocks: int


class _Design:
    """Precomputed re-draws of the treatment indicator within blocks."""

    def __init__(
        self,
        values: npt.NDArray[np.float64],
        treated: npt.NDArray[np.int8],
        accounts: npt.NDArray[np.int64],
        blocks: npt.NDArray[np.int64],
        permutations: int,
        seed: int,
    ) -> None:
        self.values = values
        self.treated = treated.astype(np.float64)
        rng = np.random.default_rng(seed)
        # int8 re-draws: permutations x units bytes (9,999 x 10,000 = ~100 MB at the cap).
        draws = np.empty((permutations, len(values)), dtype=np.int8)
        for block in np.unique(blocks):
            idx = np.flatnonzero(blocks == block)
            tiled = np.tile(treated[idx], (permutations, 1))
            draws[:, idx] = rng.permuted(tiled, axis=1)
        self.draws = draws
        self.account_masks: list[npt.NDArray[np.bool_]] = []
        weights: list[float] = []
        self.n_treated: list[float] = []
        self.n_control: list[float] = []
        for account in np.unique(accounts):
            mask = accounts == account
            n_t = float(self.treated[mask].sum())
            n_c = float(mask.sum() - n_t)
            self.account_masks.append(mask)
            self.n_treated.append(n_t)
            self.n_control.append(n_c)
            weights.append(n_c * n_t / (n_c + n_t))
        self.weights = np.asarray(weights) / float(np.sum(weights))

    def statistic(
        self, assignment: npt.NDArray[Any], y: npt.NDArray[np.float64]
    ) -> npt.NDArray[np.float64]:
        """Stratified difference for each row of ``assignment`` (2-D) or a single row.
        Rows are processed in chunks so the int8 re-draws are never copied to float whole."""
        rows = np.atleast_2d(assignment)
        total = np.zeros(rows.shape[0])
        for start in range(0, rows.shape[0], CHUNK_ROWS):
            chunk = rows[start : start + CHUNK_ROWS].astype(np.float64)
            for w, mask, n_t, n_c in zip(
                self.weights, self.account_masks, self.n_treated, self.n_control, strict=True
            ):
                ys = y[mask]
                sum_t = chunk[:, mask] @ ys
                sum_c = ys.sum() - sum_t
                total[start : start + CHUNK_ROWS] += w * (sum_t / n_t - sum_c / n_c)
        return total

    def p_value(self, shift: float) -> float:
        """Test "effect = shift" (constant-shift model): remove it from treated outcomes."""
        y = self.values - shift * self.treated
        observed = float(self.statistic(self.treated, y)[0])
        null = self.statistic(self.draws, y)
        extreme = int(np.sum(np.abs(null) >= abs(observed) - 1e-12))
        return (1 + extreme) / (len(null) + 1)


def randomization_test(
    values: Sequence[float],
    treated: Sequence[int],
    accounts: Sequence[int],
    blocks: Sequence[int],
    *,
    alpha: float,
    permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = 0,
) -> RandomizationResult | None:
    """Two-sided randomization test and inverted CI at level 1 - alpha.

    Only accounts with at least 2 units in each arm should be passed (checked here).
    Returns None when no account qualifies.
    """
    if len(values) > MAX_UNITS:
        raise ValueError(f"randomization test is capped at {MAX_UNITS} units per comparison")
    y = np.asarray(values, dtype=np.float64)
    t = np.asarray(treated, dtype=np.int8)
    acct = np.asarray(accounts, dtype=np.int64)
    blk = np.asarray(blocks, dtype=np.int64)
    keep = np.zeros(len(y), dtype=bool)
    for account in np.unique(acct):
        mask = acct == account
        if int(t[mask].sum()) >= 2 and int((1 - t[mask]).sum()) >= 2:
            keep |= mask
    if not keep.any():
        return None
    design = _Design(y[keep], t[keep], acct[keep], blk[keep], permutations, seed)
    estimate = float(design.statistic(design.treated, design.values)[0])
    p0 = design.p_value(0.0)
    spread = float(np.std(design.values)) or 1.0
    low = _bound(design, estimate, estimate - 20 * spread, alpha)
    high = _bound(design, estimate, estimate + 20 * spread, alpha)
    # Decision and interval agree at 0 in both directions (p(d) need not be monotone):
    # p0 <= alpha  => 0 outside the interval;  p0 > alpha => 0 inside it.
    if p0 <= alpha and low <= 0.0 <= high:
        if estimate > 0:
            low = max(low, np.nextafter(0.0, 1.0))
        else:
            high = min(high, np.nextafter(0.0, -1.0))
    elif p0 > alpha and not low <= 0.0 <= high:
        low, high = min(low, 0.0), max(high, 0.0)
    return RandomizationResult(
        estimate=estimate,
        p_value=p0,
        ci_low=float(low),
        ci_high=float(high),
        ci_level=1.0 - alpha,
        permutations=permutations,
        accounts=len(design.account_masks),
        blocks=len(np.unique(blk[keep])),
    )


def _bound(design: _Design, inside: float, outside: float, alpha: float) -> float:
    """Bisection for the CI edge between ``inside`` (not rejected) and ``outside``."""
    if design.p_value(outside) > alpha:
        return outside  # the bracket was too narrow: report the bracket edge (conservative)
    for _ in range(BISECTION_STEPS):
        mid = (inside + outside) / 2.0
        if design.p_value(mid) > alpha:
            inside = mid
        else:
            outside = mid
    return inside
