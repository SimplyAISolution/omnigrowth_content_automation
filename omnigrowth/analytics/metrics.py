"""Default derived metrics (spec §17) and NULL-safe helpers (DATA_MODEL §5.3, Phase 8 gate).

The defaults below are seeded as global (brand-less) ``derived_metric_definitions`` rows;
brands may add their own definitions or override a key with new revisions (OWNER only).
Every formula is data parsed by ``analytics/formula.py`` — never evaluated code (AGENTS.md
§3). Semantics, attribution scope and limitations are documented in docs/METRICS.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from omnigrowth.analytics.formula import (
    SNAPSHOT_METRICS,
    Evaluation,
    Formula,
    evaluate,
    evaluate_aggregate,
    parse_formula,
)

COUNT_METRICS = SNAPSHOT_METRICS
DerivedValue = Evaluation  # historical name used by reports and tests


@dataclass(frozen=True, slots=True)
class DefaultDefinition:
    key: str
    name: str
    description: str
    formula: Formula


def _default(
    key: str,
    name: str,
    description: str,
    expression: str,
    weights: Mapping[str, float] | None = None,
) -> DefaultDefinition:
    return DefaultDefinition(key, name, description, parse_formula(expression, weights))


DEFINITIONS: tuple[DefaultDefinition, ...] = (
    _default(
        "engagement_rate",
        "Engagement rate",
        "Interactions (likes, comments, shares, saves that were captured) per impression.",
        "captured(likes, comments, shares, saves) / impressions",
    ),
    _default("share_rate", "Share rate", "Shares per impression.", "shares / impressions"),
    _default("save_rate", "Save rate", "Saves per impression.", "saves / impressions"),
    _default("comment_rate", "Comment rate", "Comments per impression.", "comments / impressions"),
    _default("click_rate", "Click rate", "Clicks per impression.", "clicks / impressions"),
    _default(
        "follow_conversion",
        "Follow conversion",
        "Net followers gained per profile visit.",
        "followers_gained / profile_visits",
    ),
    _default(
        "content_efficiency",
        "Content efficiency",
        "Weighted outcome per unit of effort: (reach + meaningful engagement + conversions) "
        "over (1 + creation cost + production time). NOT an objective score: the weights are "
        "a starting point to tune per brand (spec §17).",
        "(w.reach * reach + w.engagement * captured(comments, shares, saves)"
        " + w.conversion * conversion_count)"
        " / (1 + w.cost * creation_cost_usd + w.time * production_minutes)",
        {"reach": 0.001, "engagement": 1.0, "conversion": 5.0, "cost": 10.0, "time": 0.1},
    ),
)
DEFINITION_KEYS = tuple(d.key for d in DEFINITIONS)


def derive(values: Mapping[str, int | float | None], definition: DefaultDefinition) -> Evaluation:
    return evaluate(definition.formula, values)


def derive_all(values: Mapping[str, int | float | None]) -> dict[str, Evaluation]:
    return {d.key: evaluate(d.formula, values) for d in DEFINITIONS}


def derive_aggregate(
    rows: list[Mapping[str, int | float | None]], definition: DefaultDefinition
) -> Evaluation:
    return evaluate_aggregate(definition.formula, rows)


def sum_counts(rows: Iterable[Mapping[str, int | None]]) -> dict[str, int | None]:
    """Column-wise totals; a metric no row captured stays None (not 0)."""
    totals: dict[str, int | None] = dict.fromkeys(COUNT_METRICS)
    for row in rows:
        for key in COUNT_METRICS:
            value = row.get(key)
            if value is not None:
                totals[key] = (totals[key] or 0) + value
    return totals
