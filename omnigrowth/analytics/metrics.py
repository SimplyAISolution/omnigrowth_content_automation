"""Derived metrics (spec §17) with NULL-safe math (DATA_MODEL §5.3, Phase 8 gate).

A missing input is never treated as 0: a rate whose denominator is missing or zero is
``None`` with a ``null_reason``; a numerator made of several counts uses the ones that were
captured and lists the missing ones, and is ``None`` only when none were captured.
Formulas are data (ratios over known metric names), never evaluated code (AGENTS.md §3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

COUNT_METRICS = (
    "impressions",
    "views",
    "reach",
    "likes",
    "comments",
    "shares",
    "saves",
    "clicks",
    "profile_visits",
    "followers_gained",
    "conversion_count",
)


@dataclass(frozen=True, slots=True)
class Ratio:
    key: str
    name: str
    numerator: tuple[str, ...]
    denominator: str


DEFINITIONS: tuple[Ratio, ...] = (
    Ratio(
        "engagement_rate",
        "Engagement rate",
        ("likes", "comments", "shares", "saves"),
        "impressions",
    ),
    Ratio("share_rate", "Share rate", ("shares",), "impressions"),
    Ratio("save_rate", "Save rate", ("saves",), "impressions"),
    Ratio("comment_rate", "Comment rate", ("comments",), "impressions"),
    Ratio("click_rate", "Click rate", ("clicks",), "impressions"),
    Ratio("follow_conversion", "Follow conversion", ("followers_gained",), "profile_visits"),
)
DEFINITION_KEYS = tuple(d.key for d in DEFINITIONS)


@dataclass(frozen=True, slots=True)
class DerivedValue:
    value: float | None
    null_reason: str | None = None
    missing_inputs: tuple[str, ...] = field(default_factory=tuple)

    def as_json(self) -> dict[str, object]:
        return {
            "value": None if self.value is None else round(self.value, 6),
            "null_reason": self.null_reason,
            "missing_inputs": list(self.missing_inputs),
        }


def derive(values: Mapping[str, int | None], ratio: Ratio) -> DerivedValue:
    denominator = values.get(ratio.denominator)
    if denominator is None:
        return DerivedValue(None, f"{ratio.denominator} not captured")
    if denominator == 0:
        return DerivedValue(None, f"{ratio.denominator} is 0")
    present = [values[k] for k in ratio.numerator if values.get(k) is not None]
    missing = tuple(k for k in ratio.numerator if values.get(k) is None)
    if not present:
        return DerivedValue(None, f"{', '.join(ratio.numerator)} not captured", missing)
    return DerivedValue(sum(v for v in present if v is not None) / denominator, None, missing)


def derive_all(values: Mapping[str, int | None]) -> dict[str, DerivedValue]:
    return {ratio.key: derive(values, ratio) for ratio in DEFINITIONS}


def derive_aggregate(rows: list[Mapping[str, int | None]], ratio: Ratio) -> DerivedValue:
    """A rate over several publications, computed only from comparable rows.

    Rows without the denominator are left out; a numerator input is used only if every
    remaining row captured it (otherwise one platform's missing ``saves`` would silently
    count as 0 against its impressions). Excluded inputs are reported in ``missing_inputs``.
    """
    usable = [r for r in rows if r.get(ratio.denominator) is not None]
    if not usable:
        return DerivedValue(None, f"{ratio.denominator} not captured")
    denominator = sum(int(r[ratio.denominator] or 0) for r in usable)
    if denominator == 0:
        return DerivedValue(None, f"{ratio.denominator} is 0")
    inputs = [k for k in ratio.numerator if all(r.get(k) is not None for r in usable)]
    missing = tuple(k for k in ratio.numerator if k not in inputs)
    if not inputs:
        return DerivedValue(
            None, f"{', '.join(ratio.numerator)} not captured for every publication", missing
        )
    numerator = sum(int(r[k] or 0) for r in usable for k in inputs)
    return DerivedValue(numerator / denominator, None, missing)


def derive_all_aggregate(rows: list[Mapping[str, int | None]]) -> dict[str, DerivedValue]:
    return {ratio.key: derive_aggregate(rows, ratio) for ratio in DEFINITIONS}


def sum_counts(rows: Iterable[Mapping[str, int | None]]) -> dict[str, int | None]:
    """Column-wise totals; a metric no row captured stays None (not 0)."""
    totals: dict[str, int | None] = dict.fromkeys(COUNT_METRICS)
    for row in rows:
        for key in COUNT_METRICS:
            value = row.get(key)
            if value is not None:
                totals[key] = (totals[key] or 0) + value
    return totals
