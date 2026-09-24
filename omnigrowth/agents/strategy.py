"""Strategy Agent (§7A): positioning, pillars, funnel stage, CTA and platform choice.

The campaign's platforms were chosen by the user; the agent may only order them. Output
naming a platform outside the campaign is clamped back to the campaign's choice.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from omnigrowth.agents.base import AgentSpec
from omnigrowth.core.enums import FunnelStage, ModelRole, Platform


class StrategyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positioning: str = Field(min_length=20, max_length=800)
    audience_summary: str = Field(min_length=10, max_length=800)
    content_pillars: list[str] = Field(min_length=1, max_length=6)
    key_messages: list[str] = Field(min_length=1, max_length=6)
    funnel_stage: FunnelStage
    primary_cta: str = Field(min_length=3, max_length=300)
    primary_platform: Platform
    secondary_platforms: list[Platform] = Field(default_factory=list, max_length=8)
    rationale: str = Field(min_length=10, max_length=1500)


SPEC = AgentSpec(
    name="strategy",
    prompt_ref="campaign/strategy@v1",
    schema=StrategyOutput,
    role=ModelRole.REASONING_LOCAL,
)


def variables(
    *, brand_block: str, campaign_block: str, research_summary: str, claims: str
) -> dict[str, str]:
    return {
        "brand": brand_block,
        "campaign": campaign_block,
        "research_summary": research_summary,
        "claims": claims,
    }


def clamp_platforms(
    output: StrategyOutput, primary: Platform, secondary: tuple[Platform, ...]
) -> StrategyOutput:
    allowed = (primary, *secondary)
    chosen = output.primary_platform if output.primary_platform in allowed else primary
    rest = [p for p in dict.fromkeys(output.secondary_platforms) if p in allowed and p != chosen]
    # Keep every user-chosen platform in play, even if the model forgot one.
    rest += [p for p in allowed if p != chosen and p not in rest]
    return output.model_copy(update={"primary_platform": chosen, "secondary_platforms": rest})


def strategy_block(output: StrategyOutput) -> str:
    return "\n".join(
        [
            f"Positioning: {output.positioning}",
            f"Audience: {output.audience_summary}",
            f"Content pillars: {'; '.join(output.content_pillars)}",
            f"Key messages: {'; '.join(output.key_messages)}",
            f"Funnel stage: {output.funnel_stage.value}",
            f"Primary CTA: {output.primary_cta}",
            f"Primary platform: {output.primary_platform.value}",
            "Secondary platforms: "
            + ("; ".join(p.value for p in output.secondary_platforms) or "(none)"),
        ]
    )
