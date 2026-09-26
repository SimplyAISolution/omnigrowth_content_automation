"""Unit tests for the Phase 5 planning agents' deterministic logic and fixture recording."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from omnigrowth.agents import opportunity, planner, research, strategy
from omnigrowth.core.enums import AngleType, ClaimLabel, Platform
from omnigrowth.prompts import PromptRegistry
from omnigrowth.providers.base import ChatRequest
from omnigrowth.providers.fake import CannedProvider, FakeProvider, FixtureMissingError

SOURCE = "Local agents keep data on the machine.\nThey   avoid per-token fees."


# ------------------------------------------------------------------ research (A5)
def test_source_stated_claim_needs_a_real_quote() -> None:
    out = research.ResearchOutput(
        summary="A short summary of the source text.",
        claims=[
            research.ExtractedClaim(
                claim="Data stays local",
                label=ClaimLabel.SOURCE_STATED,
                source_excerpt="keep DATA on the machine",
            ),
            research.ExtractedClaim(
                claim="Fees avoided",
                label=ClaimLabel.SOURCE_STATED,
                source_excerpt="They avoid per-token fees.",  # whitespace differs: still found
            ),
            research.ExtractedClaim(
                claim="Costs drop 80%",
                label=ClaimLabel.SOURCE_STATED,
                source_excerpt="costs drop 80%",
            ),
            research.ExtractedClaim(claim="Local is the future", label=ClaimLabel.OPINION),
        ],
    )
    claims = research.verify_claims(out, SOURCE)
    assert [c.label for c in claims] == [
        ClaimLabel.SOURCE_STATED,
        ClaimLabel.SOURCE_STATED,
        ClaimLabel.NEEDS_VERIFICATION,
        ClaimLabel.OPINION,
    ]
    assert [c.ordinal for c in claims] == [1, 2, 3, 4]
    assert claims[2].downgraded and claims[2].source_excerpt is None
    assert research.claims_block(claims).startswith("[1] (SOURCE_STATED) Data stays local")


# ------------------------------------------------------------------ strategy
def _strategy(primary: Platform, secondary: list[Platform]) -> strategy.StrategyOutput:
    return strategy.StrategyOutput(
        positioning="Local-first AI for small businesses.",
        audience_summary="Small-business owners",
        content_pillars=["privacy"],
        key_messages=["data stays home"],
        funnel_stage="AWARENESS",  # type: ignore[arg-type]
        primary_cta="Book a call",
        primary_platform=primary,
        secondary_platforms=secondary,
        rationale="Because it fits the audience.",
    )


def test_strategy_platforms_are_clamped_to_the_campaign() -> None:
    out = strategy.clamp_platforms(
        _strategy(Platform.REDDIT, [Platform.X, Platform.YOUTUBE]),
        Platform.LINKEDIN,
        (Platform.X, Platform.SUBSTACK),
    )
    assert out.primary_platform is Platform.LINKEDIN
    assert out.secondary_platforms == [Platform.X, Platform.SUBSTACK]


def test_strategy_may_reorder_campaign_platforms() -> None:
    out = strategy.clamp_platforms(
        _strategy(Platform.X, [Platform.LINKEDIN]), Platform.LINKEDIN, (Platform.X,)
    )
    assert out.primary_platform is Platform.X
    assert out.secondary_platforms == [Platform.LINKEDIN]


# ------------------------------------------------------------------ opportunity
def _idea(angle: AngleType, value: float, **extra: Any) -> opportunity.Idea:
    return opportunity.Idea(
        angle=angle,
        hook=f"A hook about {angle.value.lower()}",
        summary="A summary of the idea.",
        platform_targets=extra.pop("platforms", [Platform.LINKEDIN]),
        estimated_value=value,
        **extra,
    )


def _all_angles(value: float = 0.5) -> list[opportunity.Idea]:
    return [_idea(a, value) for a in AngleType]


def test_opportunity_output_requires_twenty_ideas_and_every_angle() -> None:
    ideas = _all_angles() * 3
    assert len(opportunity.OpportunityOutput(ideas=ideas).ideas) == 24
    with pytest.raises(ValidationError, match="at least 20"):
        opportunity.OpportunityOutput(ideas=_all_angles())
    missing = [i for i in ideas if i.angle is not AngleType.CASE_STUDY]
    with pytest.raises(ValidationError, match="CASE_STUDY"):
        opportunity.OpportunityOutput(ideas=missing)


def test_clean_idea_drops_foreign_platforms_and_bad_evidence() -> None:
    idea = _idea(
        AngleType.STORY, 0.5, platforms=[Platform.YOUTUBE, Platform.X], evidence_claims=[0, 2, 9]
    )
    cleaned = opportunity.clean_idea(idea, (Platform.LINKEDIN, Platform.X), claim_count=3)
    assert cleaned.platform_targets == [Platform.X]
    assert cleaned.evidence_claims == [2]
    only_foreign = _idea(AngleType.STORY, 0.5, platforms=[Platform.YOUTUBE])
    assert opportunity.clean_idea(only_foreign, (Platform.LINKEDIN,), 0).platform_targets == [
        Platform.LINKEDIN
    ]


def test_mix_selection_spreads_angles_before_repeating() -> None:
    ideas = [
        _idea(AngleType.TACTICAL, 0.9),
        _idea(AngleType.TACTICAL, 0.95),
        _idea(AngleType.STORY, 0.4),
        _idea(AngleType.OPINION, 0.6),
    ]
    picked = opportunity.select_mix(ideas, 3)
    assert [r.index for r in picked] == [1, 3, 2]  # best tactical, then other angles
    assert [r.index for r in opportunity.select_mix(ideas, 10)] == [1, 3, 2, 0]
    assert opportunity.select_mix(ideas, 3) == picked  # deterministic
    assert opportunity.selected_block(picked).splitlines()[0].startswith("I1: [TACTICAL]")


# ------------------------------------------------------------------ planner
def _entry(day: int, **extra: Any) -> dict[str, Any]:
    return {
        "day": day,
        "idea_ref": extra.pop("idea_ref", f"I{day}"),
        "platform": extra.pop("platform", "LINKEDIN"),
        "format": "TEXT_POST",
        "angle": "STORY",
        "objective": "REACH",
        "funnel_stage": "AWARENESS",
        "window_start_hour": 9,
        "window_hours": 2,
        **extra,
    }


def test_plan_must_cover_every_day_and_depend_backwards() -> None:
    week = [_entry(d) for d in range(1, 8)]
    assert len(planner.PlannerOutput.model_validate({"items": week}).items) == 7
    with pytest.raises(ValidationError, match="missing \\[7\\]"):
        planner.PlannerOutput.model_validate({"items": [*week[:6], _entry(6)]})
    forward = [*week[:6], _entry(7, depends_on=7)]
    with pytest.raises(ValidationError, match="earlier item"):
        planner.PlannerOutput.model_validate({"items": forward})


def test_schedule_computes_local_windows_and_resolves_refs() -> None:
    items = [_entry(d) for d in range(1, 8)]
    items[0]["platform"] = "REDDIT"  # not a campaign platform
    items[1]["idea_ref"] = "I42"  # no such selected idea
    plan = planner.PlannerOutput.model_validate({"items": items})
    scheduled = planner.schedule(
        plan,
        selected_count=7,
        platforms=(Platform.LINKEDIN, Platform.X),
        start=date(2026, 10, 31),  # US DST ends the next night
        timezone="America/New_York",
    )
    first, second = scheduled[0], scheduled[1]
    assert first.entry.platform is Platform.LINKEDIN
    assert first.idea_index == 0 and second.idea_index is None
    assert first.window_start == datetime(2026, 10, 31, 9, tzinfo=ZoneInfo("America/New_York"))
    assert first.window_start.utcoffset() != second.window_start.utcoffset()
    assert (scheduled[6].window_start.date() - date(2026, 10, 31)).days == 6
    assert all(s.window_end > s.window_start for s in scheduled)


def test_campaign_prompts_are_registered_private() -> None:
    registry = PromptRegistry.load()
    for spec in (research.SPEC, strategy.SPEC, opportunity.SPEC, planner.SPEC):
        prompt = registry.get(spec.prompt_ref)
        assert prompt.front_matter.privacy_class == "PRIVATE"
        assert prompt.front_matter.role == spec.role
        assert prompt.front_matter.output_schema == (
            f"{spec.schema.__module__}.{spec.schema.__name__}"
        )


# ------------------------------------------------------------------ fixture recording (A8)
def _request(prompt_id: str = "campaign/strategy") -> ChatRequest:
    return ChatRequest(
        model="m",
        messages=[{"role": "user", "content": "x"}],
        meta={"prompt_id": prompt_id, "prompt_version": 1, "input_hash": "a" * 64},
    )


async def test_record_mode_appends_canned_answers(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (canonical / "campaign__strategy.json").write_text(
        json.dumps({"responses": [{"ok": 1}, "plain text"]}), encoding="utf-8"
    )
    fixtures = tmp_path / "fixtures"
    recorder = FakeProvider("p", fixtures_dir=fixtures, record_from=CannedProvider(canonical))
    first = await recorder.chat(_request())
    second = await recorder.chat(_request())
    assert json.loads(first.text) == {"ok": 1} and second.text == "plain text"

    replay = FakeProvider("p", fixtures_dir=fixtures)
    assert (await replay.chat(_request())).text == first.text
    assert (await replay.chat(_request())).text == "plain text"
    with pytest.raises(FixtureMissingError):
        await replay.chat(_request())  # past the recorded answers: fails loudly
    with pytest.raises(FixtureMissingError):
        await FakeProvider("p", fixtures_dir=fixtures).chat(_request("campaign/planner"))


async def test_canned_provider_requires_a_canonical_file(tmp_path: Path) -> None:
    with pytest.raises(FixtureMissingError):
        await CannedProvider(tmp_path).chat(_request())


def test_trivial_or_number_dropping_quotes_do_not_prove_a_claim() -> None:
    source = "Local AI agents keep data local. Teams cut cloud fees by 40% in our pilot."
    out = research.ResearchOutput(
        summary="A short summary of the source text.",
        claims=[
            research.ExtractedClaim(
                claim="80% of teams cut costs",
                label=ClaimLabel.SOURCE_STATED,
                source_excerpt="local",
            ),
            research.ExtractedClaim(
                claim="Teams cut cloud fees by 80%",
                label=ClaimLabel.SOURCE_STATED,
                source_excerpt="Teams cut cloud fees by",
            ),
            research.ExtractedClaim(
                claim="Teams cut cloud fees by 40%",
                label=ClaimLabel.SOURCE_STATED,
                source_excerpt="Teams cut cloud fees by 40% in our pilot",
            ),
        ],
    )
    labels = [c.label for c in research.verify_claims(out, source)]
    assert labels == [
        ClaimLabel.NEEDS_VERIFICATION,
        ClaimLabel.NEEDS_VERIFICATION,
        ClaimLabel.SOURCE_STATED,
    ]


def test_schedule_replaces_formats_the_platform_does_not_support() -> None:
    from omnigrowth.core.enums import ContentFormat

    items = [_entry(d) for d in range(1, 8)]
    items[0]["format"] = "THREAD"  # not a LinkedIn format
    items[1]["platform"] = "X"
    items[1]["format"] = "CAROUSEL"  # not an X format
    plan = planner.PlannerOutput.model_validate({"items": items})
    scheduled = planner.schedule(
        plan,
        selected_count=7,
        platforms=(Platform.LINKEDIN, Platform.X),
        start=date(2026, 10, 5),
        timezone="UTC",
        formats={
            Platform.LINKEDIN: [ContentFormat.TEXT_POST, ContentFormat.CAROUSEL],
            Platform.X: [ContentFormat.THREAD, ContentFormat.TEXT_POST],
        },
    )
    assert scheduled[0].entry.format is ContentFormat.TEXT_POST
    assert scheduled[1].entry.format is ContentFormat.TEXT_POST
    assert scheduled[2].entry.format is ContentFormat.TEXT_POST  # already allowed, unchanged
