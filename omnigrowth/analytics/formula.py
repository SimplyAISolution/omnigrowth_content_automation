"""Derived-metric formulas as data (spec §17, API_PLAN §2.10; AGENTS.md §3: never ``eval``).

A formula is written as a small arithmetic expression, for example::

    captured(likes, comments, shares, saves) / impressions
    (w.reach * reach + w.engagement * saves) / (1 + w.cost * creation_cost_usd)

``parse_formula`` reads it with ``ast.parse(mode="eval")`` and accepts ONLY: numbers,
metric names from ``FORMULA_METRICS``, weights ``w.<name>`` (declared in the definition),
``+ - * /``, unary minus, parentheses and ``captured(<metric>, ...)``. Anything else (calls,
attributes, subscripts, names outside the whitelist, ``**``) is rejected. The parsed tree is
stored as JSON (``to_json``/``from_json``) and evaluated by a tiny interpreter.

NULL semantics (Phase 8 gate: no divide-by-zero, no fake zeros):
* a metric that was not captured makes the whole expression NULL with a reason, except
* ``captured(a, b, ...)`` sums the inputs that WERE captured and lists the missing ones; it
  is NULL only when none was captured;
* dividing by NULL or 0 gives NULL with a reason (never an exception, never 0).
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from omnigrowth.core.enums import MetricNullReason

# Per-publication inputs a formula may reference. Counts come from analytics snapshots;
# the last two are attributed from the generation run of the published version.
SNAPSHOT_METRICS = (
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
COST_METRICS = ("creation_cost_usd", "production_minutes")
FORMULA_METRICS = frozenset(SNAPSHOT_METRICS + COST_METRICS)

MAX_NODES = 64
MAX_DEPTH = 10
MAX_SOURCE_CHARS = 1_000
WEIGHT_NAMESPACE = "w"
_OPS: dict[type[ast.operator], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
}

Op = Literal["+", "-", "*", "/"]
RATIO_OF_SUMS = "RATIO_OF_SUMS"
MEAN_OF_ROWS = "MEAN_OF_ROWS"


class FormulaError(ValueError):
    """The formula text is not in the restricted grammar (reported as 422)."""


# ------------------------------------------------------------------ tree
@dataclass(frozen=True, slots=True)
class Const:
    value: float


@dataclass(frozen=True, slots=True)
class Metric:
    name: str


@dataclass(frozen=True, slots=True)
class Weight:
    name: str


@dataclass(frozen=True, slots=True)
class Captured:
    metrics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Neg:
    operand: Node


@dataclass(frozen=True, slots=True)
class BinOp:
    op: Op
    left: Node
    right: Node


Node = Const | Metric | Weight | Captured | Neg | BinOp


@dataclass(frozen=True, slots=True)
class Formula:
    source: str
    root: Node
    weights: Mapping[str, float] = field(default_factory=dict)

    @property
    def metrics(self) -> frozenset[str]:
        return frozenset(_metrics(self.root, captured=True))

    @property
    def strict_metrics(self) -> frozenset[str]:
        """Metrics that must be captured for a value (those outside ``captured(...)``)."""
        return frozenset(_metrics(self.root, captured=False))

    @property
    def weight_names(self) -> frozenset[str]:
        return frozenset(_weights(self.root))

    @property
    def has_constants(self) -> bool:
        return _has_const(self.root)


def _children(node: Node) -> tuple[Node, ...]:
    if isinstance(node, BinOp):
        return (node.left, node.right)
    if isinstance(node, Neg):
        return (node.operand,)
    return ()


def _metrics(node: Node, *, captured: bool) -> list[str]:
    if isinstance(node, Metric):
        return [node.name]
    if isinstance(node, Captured):
        return list(node.metrics) if captured else []
    return [m for child in _children(node) for m in _metrics(child, captured=captured)]


def _has_const(node: Node) -> bool:
    return isinstance(node, Const) or any(_has_const(c) for c in _children(node))


def _weights(node: Node) -> list[str]:
    if isinstance(node, Weight):
        return [node.name]
    return [w for child in _children(node) for w in _weights(child)]


# ------------------------------------------------------------------ parsing
def _convert(node: ast.AST, depth: int, budget: list[int]) -> Node:
    budget[0] += 1
    if budget[0] > MAX_NODES:
        raise FormulaError(f"formula has more than {MAX_NODES} parts")
    if depth > MAX_DEPTH:
        raise FormulaError(f"formula is nested deeper than {MAX_DEPTH}")
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise FormulaError("only numeric constants are allowed")
        try:
            value = float(node.value)
        except OverflowError as exc:
            raise FormulaError("constant is too large") from exc
        if not math.isfinite(value):
            raise FormulaError("constants must be finite")
        return Const(value)
    if isinstance(node, ast.Name):
        if node.id not in FORMULA_METRICS:
            raise FormulaError(f"unknown metric {node.id!r}")
        return Metric(node.id)
    if isinstance(node, ast.Attribute):
        if not (isinstance(node.value, ast.Name) and node.value.id == WEIGHT_NAMESPACE):
            raise FormulaError(f"only weights ({WEIGHT_NAMESPACE}.<name>) may use '.'")
        if not node.attr.isidentifier() or node.attr.startswith("_"):
            raise FormulaError(f"invalid weight name {node.attr!r}")
        return Weight(node.attr)
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return Neg(_convert(node.operand, depth + 1, budget))
        if isinstance(node.op, ast.UAdd):
            return _convert(node.operand, depth + 1, budget)
        raise FormulaError("only unary minus is allowed")
    if isinstance(node, ast.BinOp):
        op = _OPS.get(type(node.op))
        if op is None:
            raise FormulaError("only + - * / are allowed")
        return BinOp(
            op,  # type: ignore[arg-type]
            _convert(node.left, depth + 1, budget),
            _convert(node.right, depth + 1, budget),
        )
    if isinstance(node, ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id == "captured"):
            raise FormulaError("the only function is captured(<metric>, ...)")
        if node.keywords or not node.args:
            raise FormulaError("captured() takes one or more metric names")
        names: list[str] = []
        for arg in node.args:
            if not isinstance(arg, ast.Name) or arg.id not in FORMULA_METRICS:
                raise FormulaError("captured() takes metric names only")
            if arg.id in names:
                raise FormulaError(f"captured() lists {arg.id!r} twice")
            names.append(arg.id)
        budget[0] += len(names)
        return Captured(tuple(names))
    raise FormulaError(f"{type(node).__name__} is not allowed in a formula")


def parse_formula(source: str, weights: Mapping[str, float] | None = None) -> Formula:
    """Parse and validate. Every ``w.<name>`` must be declared in ``weights``; every declared
    weight must be used (a typo would otherwise silently do nothing)."""
    text = source.strip()
    if not text:
        raise FormulaError("formula is empty")
    if len(text) > MAX_SOURCE_CHARS:
        raise FormulaError(f"formula is longer than {MAX_SOURCE_CHARS} characters")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"formula is not a valid expression: {exc.msg}") from exc
    root = _convert(tree.body, 0, [0])
    declared = dict(weights or {})
    for name, value in declared.items():
        if not math.isfinite(value):
            raise FormulaError(f"weight {name!r} must be finite")
    formula = Formula(text, root, declared)
    undeclared = formula.weight_names - declared.keys()
    if undeclared:
        raise FormulaError(f"weights used but not declared: {sorted(undeclared)}")
    unused = declared.keys() - formula.weight_names
    if unused:
        raise FormulaError(f"weights declared but not used: {sorted(unused)}")
    if not formula.metrics:
        raise FormulaError("formula references no metric")
    return formula


# ------------------------------------------------------------------ JSON storage
def node_to_json(node: Node) -> dict[str, Any]:
    if isinstance(node, Const):
        return {"const": node.value}
    if isinstance(node, Metric):
        return {"metric": node.name}
    if isinstance(node, Weight):
        return {"weight": node.name}
    if isinstance(node, Captured):
        return {"captured": list(node.metrics)}
    if isinstance(node, Neg):
        return {"neg": node_to_json(node.operand)}
    return {"op": node.op, "left": node_to_json(node.left), "right": node_to_json(node.right)}


def to_json(formula: Formula) -> dict[str, Any]:
    return {
        "expression": formula.source,
        "weights": dict(formula.weights),
        "ast": node_to_json(formula.root),
    }


def from_json(stored: Mapping[str, Any]) -> Formula:
    """Re-parse from the stored expression (the AST is kept for readers and audits); a stored
    formula that no longer validates fails loudly instead of being evaluated."""
    return parse_formula(str(stored["expression"]), stored.get("weights") or {})


# ------------------------------------------------------------------ evaluation
Values = Mapping[str, float | int | None]


@dataclass(frozen=True, slots=True)
class Evaluation:
    value: float | None
    null_reason: str | None = None
    missing_inputs: tuple[str, ...] = ()
    null_code: MetricNullReason | None = None
    aggregation: str | None = None  # set on aggregates: RATIO_OF_SUMS | MEAN_OF_ROWS

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "value": None if self.value is None else round(self.value, 6),
            "null_reason": self.null_reason,
            "null_code": self.null_code.value if self.null_code else None,
            "missing_inputs": list(self.missing_inputs),
        }
        if self.aggregation is not None:
            out["aggregation"] = self.aggregation
        return out


class _Null(Exception):  # noqa: N818 - internal control flow
    def __init__(
        self, reason: str, code: MetricNullReason = MetricNullReason.MISSING_INPUT
    ) -> None:
        self.reason = reason
        self.code = code


def _describe(node: Node) -> str:
    if isinstance(node, Metric):
        return node.name
    if isinstance(node, Captured):
        return ", ".join(node.metrics)
    return "the denominator"


def _eval(
    node: Node,
    values: Values,
    weights: Mapping[str, float],
    missing: list[str],
    allowed: frozenset[str] | None,
) -> float:
    if isinstance(node, Const):
        return node.value
    if isinstance(node, Weight):
        return weights[node.name]
    if isinstance(node, Metric):
        value = values.get(node.name)
        if value is None:
            raise _Null(f"{node.name} not captured")
        return float(value)
    if isinstance(node, Captured):
        usable = [m for m in node.metrics if allowed is None or m in allowed]
        present = [float(v) for m in usable if (v := values.get(m)) is not None]
        missing.extend(m for m in node.metrics if m not in usable or values.get(m) is None)
        if not present:
            raise _Null(f"{', '.join(node.metrics)} not captured")
        return math.fsum(present)
    if isinstance(node, Neg):
        return -_eval(node.operand, values, weights, missing, allowed)
    if node.op == "/":
        # Denominator first: "impressions not captured" / "impressions is 0" is the reason
        # a reader needs, whatever the numerator holds.
        right = _eval(node.right, values, weights, missing, allowed)
        if right == 0:
            raise _Null(f"{_describe(node.right)} is 0", MetricNullReason.ZERO_DENOMINATOR)
        return _eval(node.left, values, weights, missing, allowed) / right
    left = _eval(node.left, values, weights, missing, allowed)
    right = _eval(node.right, values, weights, missing, allowed)
    if node.op == "+":
        return left + right
    if node.op == "-":
        return left - right
    return left * right


def evaluate(
    formula: Formula, values: Values, *, allowed: frozenset[str] | None = None
) -> Evaluation:
    """Evaluate for one publication. ``allowed`` restricts which ``captured()`` inputs may
    be used (aggregation passes the inputs every row captured)."""
    missing: list[str] = []
    try:
        result = _eval(formula.root, values, formula.weights, missing, allowed)
    except _Null as exc:
        return Evaluation(None, exc.reason, tuple(dict.fromkeys(missing)), exc.code)
    if not math.isfinite(result):
        # Only reachable through a denominator that rounds to 0 (e.g. 1 - 0.999999...).
        return Evaluation(
            None,
            "result is not a finite number",
            tuple(dict.fromkeys(missing)),
            MetricNullReason.ZERO_DENOMINATOR,
        )
    return Evaluation(result, None, tuple(dict.fromkeys(missing)))


def evaluate_aggregate(formula: Formula, rows: Sequence[Values]) -> Evaluation:
    """One value over several publications, from comparable rows only.

    Rows missing any strictly-required metric are left out. Inside ``captured()`` an input
    is used only if EVERY remaining row captured it (one platform's missing ``saves`` must
    not count as 0 against its impressions).

    * Formulas without numeric constants are homogeneous in the counts: inputs are summed
      and the formula applied once (``ratio_of_sums``: sum(clicks) / sum(impressions), not a
      mean of rates).
    * Formulas WITH a constant (e.g. ``/ (1 + cost)``) are not: summing would count the
      constant once for N posts and make the value grow with group size. They aggregate as
      the mean of the per-publication values (``mean_of_rows``), over rows with a value.
    """
    strict = formula.strict_metrics
    usable = [r for r in rows if all(r.get(m) is not None for m in strict)]
    if not usable:
        missing_strict = sorted(m for m in strict if any(r.get(m) is None for r in rows))
        reason = (
            f"{', '.join(missing_strict)} not captured" if missing_strict else "no publications"
        )
        return Evaluation(None, reason, null_code=MetricNullReason.MISSING_INPUT)
    # A metric used strictly is present in every usable row, so it is also usable inside
    # captured(); optional-only metrics must be captured by every row.
    allowed = strict | frozenset(
        m for m in formula.metrics - strict if all(r.get(m) is not None for r in usable)
    )
    if formula.has_constants:
        results = [evaluate(formula, r, allowed=allowed) for r in usable]
        values = [e.value for e in results if e.value is not None]
        missing = tuple(dict.fromkeys(m for e in results for m in e.missing_inputs))
        if not values:
            first = results[0]
            return Evaluation(None, first.null_reason, missing, first.null_code, MEAN_OF_ROWS)
        return Evaluation(math.fsum(values) / len(values), None, missing, None, MEAN_OF_ROWS)
    totals: dict[str, float | None] = {}
    for name in formula.metrics:
        present = [float(v) for r in usable if (v := r.get(name)) is not None]
        totals[name] = math.fsum(present) if present else None
    summed = evaluate(formula, totals, allowed=allowed)
    return Evaluation(
        summed.value, summed.null_reason, summed.missing_inputs, summed.null_code, RATIO_OF_SUMS
    )
