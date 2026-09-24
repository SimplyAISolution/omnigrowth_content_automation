"""Research Agent (§7B as amended by A5): claim extraction from the source, no web access.

Every claim carries a ``ClaimLabel``. A ``SOURCE_STATED`` label is only kept when the quoted
``source_excerpt`` really occurs in the source text (whitespace/case-insensitive); otherwise
the claim is downgraded to ``NEEDS_VERIFICATION``. The agent never produces citations: with
the ``NullResearchProvider`` (A5) there is nothing to cite, so citation columns stay NULL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from omnigrowth.agents.base import AgentSpec
from omnigrowth.core.enums import ClaimLabel, ModelRole

RESEARCH_PROVIDER = "null"  # A5 NullResearchProvider


class ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=5, max_length=600)
    label: ClaimLabel
    source_excerpt: str | None = Field(default=None, max_length=600)


class ResearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=20, max_length=2000)
    claims: list[ExtractedClaim] = Field(min_length=1, max_length=30)
    opposing_perspectives: list[str] = Field(default_factory=list, max_length=10)
    open_questions: list[str] = Field(default_factory=list, max_length=10)


SPEC = AgentSpec(
    name="research",
    prompt_ref="campaign/research@v1",
    schema=ResearchOutput,
    role=ModelRole.REASONING_LOCAL,
)


def variables(*, brand_block: str, campaign_block: str, source_text: str) -> dict[str, str]:
    return {"brand": brand_block, "campaign": campaign_block, "source": source_text}


_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
# A quote shorter than this cannot carry a claim ("local" is in every source).
MIN_EXCERPT_CHARS = 20


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


@dataclass(frozen=True, slots=True)
class VerifiedClaim:
    ordinal: int
    claim: str
    label: ClaimLabel
    source_excerpt: str | None
    downgraded: bool


def _supports(excerpt: str, claim: str, haystack: str) -> bool:
    """The excerpt is a real, non-trivial quote that carries every number in the claim."""
    quote = _norm(excerpt)
    if len(quote) < MIN_EXCERPT_CHARS or quote not in haystack:
        return False
    return set(_NUMBER.findall(claim)) <= set(_NUMBER.findall(quote))


def verify_claims(output: ResearchOutput, source_text: str) -> list[VerifiedClaim]:
    """Apply the A5 provenance rule; ordinals are 1-based and stable.

    ``SOURCE_STATED`` survives only when the excerpt is a verbatim quote of at least
    ``MIN_EXCERPT_CHARS`` characters that also contains every number the claim states.
    """
    haystack = _norm(source_text)
    verified: list[VerifiedClaim] = []
    for ordinal, claim in enumerate(output.claims, start=1):
        excerpt = claim.source_excerpt.strip() if claim.source_excerpt else None
        found = bool(excerpt) and _norm(excerpt or "") in haystack
        supported = bool(excerpt) and _supports(excerpt or "", claim.claim, haystack)
        downgraded = claim.label is ClaimLabel.SOURCE_STATED and not supported
        verified.append(
            VerifiedClaim(
                ordinal=ordinal,
                claim=claim.claim.strip(),
                label=ClaimLabel.NEEDS_VERIFICATION if downgraded else claim.label,
                # Keep only excerpts that are real quotes from the source.
                source_excerpt=excerpt if found else None,
                downgraded=downgraded,
            )
        )
    return verified


def claims_block(claims: list[VerifiedClaim]) -> str:
    """Numbered claim list for downstream prompts (ordinals double as evidence refs)."""
    return "\n".join(f"[{c.ordinal}] ({c.label.value}) {c.claim}" for c in claims)
