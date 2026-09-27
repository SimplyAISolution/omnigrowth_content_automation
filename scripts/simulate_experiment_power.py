"""Reproducible power / type-I study of the experiment decision test (Phase 9 item 4).

Checks whether the planned sample size (normal-approximation formula in
analytics/experiment_stats.required_sample) delivers the planned power, and whether the
randomization test keeps its error rate, when posts from the same account are DEPENDENT.
Units are allocated with the production routine (services/experiments.allocate).

Scenarios (two accounts, per-post noise SD 1, account levels SD 3, follower-growth trend):
  independent, AR(1) rho=+0.5, AR(1) rho=+0.8, AR(1) rho=-0.3 (alternating over/under),
  level shift mid-way through each account, effect differing by account (0.3 vs 0.9).
Welch (independence) is computed alongside for comparison.

Usage: ``uv run python scripts/simulate_experiment_power.py [--reps 300]``
Writes docs/audit/experiment_power_simulation_<UTC>.json (never overwrites).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnigrowth.analytics.experiment_stats import compare, required_sample
from omnigrowth.analytics.randomization import randomization_test
from omnigrowth.services.experiments import _run_sizes, allocate

EFFECT = 0.6
VARIANTS = [uuid.UUID(int=1), uuid.UUID(int=2)]

SCENARIOS: dict[str, dict[str, Any]] = {
    "independent": {"rho": 0.0},
    "ar1_+0.5": {"rho": 0.5},
    "ar1_+0.8": {"rho": 0.8},
    "ar1_-0.3": {"rho": -0.3},
    "level_shift": {"rho": 0.0, "shift": 2.0},
    "heterogeneous_effect": {"rho": 0.3, "effects": (0.3, 0.9)},
}


def _one(effect: float, n: int, spec: dict[str, Any], seed: int) -> tuple[bool, bool]:
    rng = random.Random(seed)  # noqa: S311 - simulation
    rho = float(spec["rho"])
    values: list[float] = []
    treated: list[int] = []
    accounts: list[int] = []
    blocks: list[int] = []
    control: dict[str, list[float]] = {}
    treatment: dict[str, list[float]] = {}
    for account in (0, 1):
        level = rng.gauss(0, 3)
        noise = 0.0
        sizes = _run_sizes(seed, f"a{account}", 2)
        run, start, size = 0, 0, next(sizes)
        account_effect = effect
        if "effects" in spec and effect:
            account_effect = spec["effects"][account] * (effect / EFFECT)
        for seq in range(n):
            while seq >= start + size:
                start += size
                size = next(sizes)
                run += 1
            arm = allocate(seed, f"a{account}", seq, VARIANTS) == VARIANTS[1]
            noise = rho * noise + math.sqrt(1 - rho * rho) * rng.gauss(0, 1)
            shift = spec.get("shift", 0.0) if seq >= n // 2 else 0.0
            value = level + 0.02 * seq + shift + noise + (account_effect if arm else 0.0)
            values.append(value)
            treated.append(int(arm))
            accounts.append(account)
            blocks.append(account * 1_000_000 + run)
            (treatment if arm else control).setdefault(str(account), []).append(value)
    rand = randomization_test(
        values, treated, accounts, blocks, alpha=0.05, permutations=499, seed=seed
    )
    welch = compare(control, treatment, ci_level=0.95)
    if rand is None or welch is None:
        raise RuntimeError("simulation produced an unusable design")
    return rand.p_value <= 0.05, welch.p_value < 0.05


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=300)
    args = parser.parse_args()
    base = required_sample(sigma=1.0, delta=EFFECT, alpha=0.05, power=0.8, comparisons=1, floor=30)
    plan = required_sample(
        sigma=1.0, delta=EFFECT, alpha=0.05, power=0.8, comparisons=1, floor=30, inflation=1.25
    )
    n = plan.required_per_variant
    n_base = base.required_per_variant
    out: dict[str, Any] = {
        "ran_at": datetime.now(UTC).isoformat(),
        "script": "scripts/simulate_experiment_power.py",
        "reps_per_cell": args.reps,
        "planned_n_per_variant": n,
        "independence_formula_n_per_variant": n_base,
        "power_inflation": 1.25,
        "plan": {"sigma": 1.0, "delta": EFFECT, "alpha": 0.05, "power": 0.8},
        "monte_carlo_se": {
            "power_near_0.8": round(math.sqrt(0.16 / args.reps), 3),
            "type_I_near_0.05": round(math.sqrt(0.0475 / args.reps), 3),
        },
        "scenarios": {},
    }
    for name, spec in SCENARIOS.items():
        power = [_one(EFFECT, n, spec, 1000 + r) for r in range(args.reps)]
        null = [_one(0.0, n, spec, 5000 + r) for r in range(args.reps)]
        base_power = [_one(EFFECT, n_base, spec, 3000 + r)[0] for r in range(args.reps)]
        out["scenarios"][name] = {
            "spec": spec,
            "randomization_power_without_inflation": sum(base_power) / args.reps,
            "randomization_power": sum(p for p, _ in power) / args.reps,
            "randomization_type_I": sum(p for p, _ in null) / args.reps,
            "welch_power": sum(w for _, w in power) / args.reps,
            "welch_type_I": sum(w for _, w in null) / args.reps,
        }
        print(name, out["scenarios"][name])
    path = (
        Path("docs/audit") / f"experiment_power_simulation_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    )
    with path.open("x", encoding="utf-8") as fh:  # never overwrite an earlier record
        fh.write(json.dumps(out, indent=2) + "\n")
    print("written", path)


if __name__ == "__main__":
    main()
