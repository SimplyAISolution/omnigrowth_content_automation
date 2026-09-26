"""Phase 8 gate: derived-metric math against hand-computed fixtures.

Every expected value below was computed by hand (shown in the comment next to it). NULL
inputs must never turn into 0 and a zero/NULL denominator must never raise.
"""

from __future__ import annotations

import math

import pytest

from omnigrowth.analytics.formula import (
    FormulaError,
    evaluate,
    evaluate_aggregate,
    from_json,
    parse_formula,
    to_json,
)
from omnigrowth.analytics.metrics import DEFINITIONS, derive_all
from omnigrowth.core.enums import MetricNullReason

BY_KEY = {d.key: d for d in DEFINITIONS}


# ------------------------------------------------------------------ grammar (never eval)
@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').system('echo hi')",
        "impressions ** 2",
        "impressions if likes else 0",
        "[likes][0]",
        "likes.real",
        "os.getcwd()",
        "lambda: 1",
        "likes < impressions",
        "likes and impressions",
        "'text'",
        "True + likes",
        "unknown_metric / impressions",
        "captured(likes, 3)",
        "captured(likes, likes)",
        "captured()",
        "sum(likes)",
        "w._secret * likes",
        "1 / 2",
        "",
        "likes /",
    ],
)
def test_anything_outside_the_grammar_is_rejected(text: str) -> None:
    with pytest.raises(FormulaError):
        parse_formula(text)


def test_weights_must_be_declared_used_and_finite() -> None:
    with pytest.raises(FormulaError, match="not declared"):
        parse_formula("w.reach * reach")
    with pytest.raises(FormulaError, match="not used"):
        parse_formula("reach", {"reach": 1.0})
    with pytest.raises(FormulaError, match="finite"):
        parse_formula("w.reach * reach", {"reach": math.inf})


def test_size_limits() -> None:
    def balanced(leaves: int) -> str:  # depth ~log2(leaves), so only the size limit trips
        if leaves == 1:
            return "likes"
        return f"({balanced(leaves // 2)} + {balanced(leaves - leaves // 2)})"

    parse_formula(balanced(30))  # 59 nodes: fine
    with pytest.raises(FormulaError, match="parts"):
        parse_formula(balanced(40))
    with pytest.raises(FormulaError, match="nested"):
        parse_formula("(" * 12 + "likes" + ")" * 12 + " + -" * 12 + "likes")


def test_json_round_trip_keeps_the_formula() -> None:
    formula = parse_formula("(w.a * reach + saves) / (1 + impressions)", {"a": 0.5})
    stored = to_json(formula)
    assert stored["ast"]["op"] == "/"
    again = from_json(stored)
    assert again.source == formula.source and dict(again.weights) == {"a": 0.5}
    assert evaluate(again, {"reach": 100, "saves": 10, "impressions": 9}).value == 6.0


# ------------------------------------------------------------------ hand-computed values
ROW = {
    "impressions": 2_000,
    "reach": 1_500,
    "likes": 80,
    "comments": 12,
    "shares": 6,
    "saves": 2,
    "clicks": 40,
    "profile_visits": 50,
    "followers_gained": 5,
    "conversion_count": 3,
    "creation_cost_usd": 0.0,
    "production_minutes": 2.5,
}


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("engagement_rate", 0.05),  # (80+12+6+2) / 2000 = 100/2000
        ("share_rate", 0.003),  # 6 / 2000
        ("save_rate", 0.001),  # 2 / 2000
        ("comment_rate", 0.006),  # 12 / 2000
        ("click_rate", 0.02),  # 40 / 2000
        ("follow_conversion", 0.1),  # 5 / 50
        # (0.001*1500 + 1*(12+6+2) + 5*3) / (1 + 10*0 + 0.1*2.5)
        # = (1.5 + 20 + 15) / 1.25 = 36.5 / 1.25 = 29.2
        ("content_efficiency", 29.2),
    ],
)
def test_default_metrics_match_hand_computation(key: str, expected: float) -> None:
    result = evaluate(BY_KEY[key].formula, ROW)
    assert result.value == pytest.approx(expected)
    assert result.null_reason is None and result.null_code is None


def test_null_denominator_is_null_with_a_reason_never_zero() -> None:
    result = evaluate(BY_KEY["share_rate"].formula, {"shares": 4, "impressions": None})
    assert result.value is None
    assert result.null_reason == "impressions not captured"
    assert result.null_code is MetricNullReason.MISSING_INPUT


def test_zero_denominator_is_null_not_an_exception() -> None:
    result = evaluate(
        BY_KEY["follow_conversion"].formula, {"followers_gained": 3, "profile_visits": 0}
    )
    assert result.value is None
    assert result.null_reason == "profile_visits is 0"
    assert result.null_code is MetricNullReason.ZERO_DENOMINATOR


def test_nested_zero_denominator() -> None:
    formula = parse_formula("likes / (impressions - reach)")
    result = evaluate(formula, {"likes": 1, "impressions": 5, "reach": 5})
    assert result.value is None and result.null_code is MetricNullReason.ZERO_DENOMINATOR


def test_missing_numerator_is_null_not_zero() -> None:
    result = evaluate(BY_KEY["click_rate"].formula, {"impressions": 1_000})
    assert result.value is None and result.null_reason == "clicks not captured"


def test_captured_uses_only_captured_inputs_and_lists_the_rest() -> None:
    # likes 30 + saves 10 over 400 impressions; comments/shares missing -> 40/400 = 0.1
    result = evaluate(
        BY_KEY["engagement_rate"].formula, {"impressions": 400, "likes": 30, "saves": 10}
    )
    assert result.value == pytest.approx(0.1)
    assert set(result.missing_inputs) == {"comments", "shares"}


def test_content_efficiency_with_unknown_cost_is_null() -> None:
    row = {**ROW, "creation_cost_usd": None}
    result = evaluate(BY_KEY["content_efficiency"].formula, row)
    assert result.value is None and result.null_reason == "creation_cost_usd not captured"


def test_negative_net_followers_are_kept() -> None:
    # -2 / 40 = -0.05 (followers_gained is net and may be negative)
    result = evaluate(
        BY_KEY["follow_conversion"].formula, {"followers_gained": -2, "profile_visits": 40}
    )
    assert result.value == pytest.approx(-0.05)


def test_derive_all_returns_every_default() -> None:
    assert set(derive_all(ROW)) == {d.key for d in DEFINITIONS}


# ------------------------------------------------------------------ aggregation
def test_aggregate_is_ratio_of_sums_not_mean_of_ratios() -> None:
    rows = [{"clicks": 1, "impressions": 10}, {"clicks": 99, "impressions": 990}]
    # sum: 100 / 1000 = 0.1 ; mean of ratios would also be 0.1 here, so use skewed rows:
    skewed = [{"clicks": 5, "impressions": 10}, {"clicks": 10, "impressions": 990}]
    # 15 / 1000 = 0.015 (mean of ratios would be (0.5 + 0.0101)/2 = 0.255)
    assert evaluate_aggregate(BY_KEY["click_rate"].formula, rows).value == pytest.approx(0.1)
    assert evaluate_aggregate(BY_KEY["click_rate"].formula, skewed).value == pytest.approx(0.015)


def test_aggregate_skips_rows_without_the_denominator() -> None:
    rows = [
        {"clicks": 10, "impressions": 100},
        {"clicks": 50, "impressions": None},  # left out: denominator not captured
    ]
    # 10 / 100 = 0.1
    assert evaluate_aggregate(BY_KEY["click_rate"].formula, rows).value == pytest.approx(0.1)
    none = evaluate_aggregate(BY_KEY["click_rate"].formula, [{"clicks": 3}])
    assert none.value is None and none.null_reason == "impressions not captured"
    empty = evaluate_aggregate(BY_KEY["click_rate"].formula, [])
    assert empty.value is None


def test_aggregate_captured_inputs_must_be_present_in_every_row() -> None:
    linkedin = {"impressions": 1_000, "likes": 50, "saves": None}
    x = {"impressions": 1_000, "likes": 30, "saves": 20}
    result = evaluate_aggregate(BY_KEY["engagement_rate"].formula, [linkedin, x])
    # saves missing for LinkedIn -> excluded for all: (50 + 30) / 2000 = 0.04
    assert result.value == pytest.approx(0.04)
    assert "saves" in result.missing_inputs


def test_aggregate_zero_total_denominator() -> None:
    rows = [{"clicks": 0, "impressions": 0}, {"clicks": 0, "impressions": 0}]
    result = evaluate_aggregate(BY_KEY["click_rate"].formula, rows)
    assert result.value is None and result.null_code is MetricNullReason.ZERO_DENOMINATOR


# ------------------------------------------------------------------ review regressions
def test_metric_used_strictly_and_inside_captured_stays_in_the_aggregate() -> None:
    formula = parse_formula("captured(likes, shares) / likes")
    rows = [{"likes": 2, "shares": 3}, {"likes": 4, "shares": 5}]
    # (2 + 4 + 3 + 5) / (2 + 4) = 14 / 6
    result = evaluate_aggregate(formula, rows)
    assert result.value == pytest.approx(14 / 6)
    assert result.missing_inputs == ()


def test_formula_with_constants_aggregates_as_mean_so_group_size_does_not_matter() -> None:
    efficiency = BY_KEY["content_efficiency"].formula
    one = evaluate_aggregate(efficiency, [ROW])
    ten = evaluate_aggregate(efficiency, [ROW] * 10)
    assert one.value == pytest.approx(29.2) and ten.value == pytest.approx(29.2)
    assert ten.aggregation == "MEAN_OF_ROWS"
    assert evaluate_aggregate(BY_KEY["click_rate"].formula, [ROW]).aggregation == "RATIO_OF_SUMS"
    # (29.2 + value with 2x the minutes) / 2 ; second row: 36.5 / (1 + 0.1*5) = 24.333...
    mixed = evaluate_aggregate(efficiency, [ROW, {**ROW, "production_minutes": 5.0}])
    assert mixed.value == pytest.approx((29.2 + 36.5 / 1.5) / 2)


def test_huge_integer_constant_is_a_formula_error_not_a_crash() -> None:
    with pytest.raises(FormulaError, match="too large"):
        parse_formula("1" + "0" * 400 + " * likes")
