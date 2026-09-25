"""Optimization Agent (§55 step 16, decision D1): evidence-grounded recommendations.

The model sees the campaign report with each analytics snapshot labelled ``S1..Sn`` (no
ids, so fixture hashes are stable) and must cite labels for every recommendation. Labels
are mapped back to ``analytics_snapshots`` ids by the caller; a recommendation citing no
existing snapshot is dropped, never stored (Phase 9 gate rule, enforced from Phase 7).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from omnigrowth.agents.base import AgentSpec
from omnigrowth.core.enums import ModelRole

LABEL = re.compile(r"^S[1-9][0-9]*$")


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=10, max_length=600)
    rationale: str = Field(min_length=10, max_length=2000)
    target: str | None = Field(default=None, max_length=200)
    evidence: list[str] = Field(min_length=1, max_length=12)


class OptimizerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendations: list[Recommendation] = Field(min_length=1, max_length=8)


SPEC = AgentSpec(
    name="optimizer",
    prompt_ref="analytics/optimizer@v1",
    schema=OptimizerOutput,
    role=ModelRole.REASONING_LOCAL,
)


def variables(*, campaign: str, report: str) -> dict[str, str]:
    return {"campaign": campaign, "report": report}


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def report_table(rows: list[dict[str, object]]) -> str:
    """Rows carry ``label`` plus content attributes, raw metrics and derived rates."""
    columns = (
        "label",
        "platform",
        "format",
        "angle",
        "hook_type",
        "cta_type",
        "word_count",
        "impressions",
        "likes",
        "comments",
        "shares",
        "saves",
        "clicks",
        "engagement_rate",
        "click_rate",
        "share_rate",
    )
    lines = [" | ".join(columns)]
    lines += [" | ".join(_fmt(row.get(c)) for c in columns) for row in rows]
    return "\n".join(lines)
