"""Opportunity Agent (§7C): at least 20 content ideas covering all eight ``AngleType``s.

The coverage rule is part of the schema, so a model that skips an angle goes through the
normal repair loop (``generate_structured``) instead of silently producing a thin plan.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omnigrowth.agents.base import AgentSpec
from omnigrowth.core.enums import AngleType, ModelRole, Platform

MIN_IDEAS = 20


class Idea(BaseModel):
    model_config = ConfigDict(extra="forbid")

    angle: AngleType
    hook: str = Field(min_length=10, max_length=300)
    summary: str = Field(min_length=10, max_length=800)
    platform_targets: list[Platform] = Field(min_length=1, max_length=8)
    estimated_value: float = Field(ge=0.0, le=1.0)
    evidence_claims: list[int] = Field(default_factory=list, max_length=6)


class OpportunityOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ideas: list[Idea] = Field(min_length=MIN_IDEAS, max_length=40)

    @model_validator(mode="after")
    def _covers_every_angle(self) -> OpportunityOutput:
        missing = sorted(set(AngleType) - {idea.angle for idea in self.ideas})
        if missing:
            raise ValueError(f"ideas must cover every angle type; missing: {missing}")
        return self


SPEC = AgentSpec(
    name="opportunity",
    prompt_ref="campaign/opportunity@v1",
    schema=OpportunityOutput,
    role=ModelRole.REASONING_LOCAL,
)


def variables(
    *, brand_block: str, campaign_block: str, strategy: str, claims: str, open_questions: str
) -> dict[str, str]:
    return {
        "brand": brand_block,
        "campaign": campaign_block,
        "strategy": strategy,
        "claims": claims,
        "open_questions": open_questions,
    }


def clean_idea(idea: Idea, platforms: tuple[Platform, ...], claim_count: int) -> Idea:
    """Drop platforms outside the campaign and evidence refs to non-existent claims."""
    targets = [p for p in dict.fromkeys(idea.platform_targets) if p in platforms]
    evidence = sorted({n for n in idea.evidence_claims if 1 <= n <= claim_count})
    return idea.model_copy(
        update={"platform_targets": targets or [platforms[0]], "evidence_claims": evidence}
    )


@dataclass(frozen=True, slots=True)
class Ranked:
    index: int  # position in the agent output (0-based)
    idea: Idea


def select_mix(ideas: list[Idea], count: int) -> list[Ranked]:
    """Deterministic mix selection: best idea per angle first (round-robin by value), so the
    plan spreads across angles before repeating one. Ties break on output order."""
    by_angle: dict[AngleType, list[Ranked]] = {}
    for index, idea in enumerate(ideas):
        by_angle.setdefault(idea.angle, []).append(Ranked(index, idea))
    for bucket in by_angle.values():
        bucket.sort(key=lambda r: (-r.idea.estimated_value, r.index))
    # Angles ordered by their best idea's value, then enum order.
    order = sorted(
        by_angle,
        key=lambda a: (-by_angle[a][0].idea.estimated_value, list(AngleType).index(a)),
    )
    chosen: list[Ranked] = []
    depth = 0
    while len(chosen) < count and any(len(by_angle[a]) > depth for a in order):
        for angle in order:
            if len(chosen) == count:
                break
            if len(by_angle[angle]) > depth:
                chosen.append(by_angle[angle][depth])
        depth += 1
    return chosen


def selected_block(selected: list[Ranked]) -> str:
    """``I1..In`` references the planner must use; ids never enter the prompt."""
    return "\n".join(
        f"I{n}: [{r.idea.angle.value}] {r.idea.hook} — {r.idea.summary} "
        f"(platforms: {', '.join(p.value for p in r.idea.platform_targets)})"
        for n, r in enumerate(selected, start=1)
    )
