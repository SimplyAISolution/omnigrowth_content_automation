"""Content Planner Agent (§7D): a 7-day plan from the selected idea mix.

The model works in relative days (1..7) and local hours; absolute publication windows are
computed here from the campaign start date and timezone, so prompts (and fixture hashes)
never contain dates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omnigrowth.agents.base import AgentSpec
from omnigrowth.core.enums import (
    AngleType,
    CampaignObjective,
    ContentFormat,
    FunnelStage,
    ModelRole,
    Platform,
)

HORIZON_DAYS = 7


class PlanEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1, le=HORIZON_DAYS)
    idea_ref: str = Field(pattern=r"^I[1-9][0-9]*$")
    platform: Platform
    format: ContentFormat
    angle: AngleType
    objective: CampaignObjective
    funnel_stage: FunnelStage
    cta: str | None = Field(default=None, max_length=300)
    window_start_hour: int = Field(ge=0, le=23)
    window_hours: int = Field(ge=1, le=12)
    depends_on: int | None = Field(default=None, ge=1)


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PlanEntry] = Field(min_length=HORIZON_DAYS, max_length=3 * HORIZON_DAYS)

    @model_validator(mode="after")
    def _well_formed(self) -> PlannerOutput:
        missing = sorted(set(range(1, HORIZON_DAYS + 1)) - {i.day for i in self.items})
        if missing:
            raise ValueError(f"every day 1..{HORIZON_DAYS} needs an item; missing {missing}")
        for number, item in enumerate(self.items, start=1):
            if item.depends_on is not None and item.depends_on >= number:
                raise ValueError(f"item {number}: depends_on must reference an earlier item number")
        return self


SPEC = AgentSpec(
    name="planner",
    prompt_ref="campaign/planner@v1",
    schema=PlannerOutput,
    role=ModelRole.REASONING_LOCAL,
)


def variables(
    *, campaign_block: str, strategy: str, selected_ideas: str, platforms: str
) -> dict[str, str]:
    return {
        "campaign": campaign_block,
        "strategy": strategy,
        "selected_ideas": selected_ideas,
        "platforms": platforms,
    }


@dataclass(frozen=True, slots=True)
class ScheduledEntry:
    ordinal: int  # 1-based, matches the agent's item number
    entry: PlanEntry
    idea_index: int | None  # 0-based index into the selected mix, None if unresolved
    window_start: datetime
    window_end: datetime


def schedule(
    output: PlannerOutput,
    *,
    selected_count: int,
    platforms: tuple[Platform, ...],
    start: date,
    timezone: str,
    formats: Mapping[Platform, Sequence[ContentFormat]] | None = None,
) -> list[ScheduledEntry]:
    """Resolve idea refs, clamp platforms to the campaign, keep formats the platform
    supports (``formats``: allowed formats for platforms that have writers/QA rules) and
    compute absolute windows."""
    tz = ZoneInfo(timezone)
    scheduled: list[ScheduledEntry] = []
    for ordinal, entry in enumerate(output.items, start=1):
        ref = int(entry.idea_ref[1:])
        idea_index = ref - 1 if ref <= selected_count else None
        if entry.platform not in platforms:
            entry = entry.model_copy(update={"platform": platforms[0]})
        allowed = (formats or {}).get(entry.platform)
        if allowed and entry.format not in allowed:
            # e.g. a LinkedIn "THREAD": use the platform's plain post instead of planning
            # content QA would reject as UNSUPPORTED_FORMAT.
            fallback = ContentFormat.TEXT_POST if ContentFormat.TEXT_POST in allowed else allowed[0]
            entry = entry.model_copy(update={"format": fallback})
        day = start + timedelta(days=entry.day - 1)
        window_start = datetime.combine(day, time(entry.window_start_hour), tzinfo=tz)
        scheduled.append(
            ScheduledEntry(
                ordinal=ordinal,
                entry=entry,
                idea_index=idea_index,
                window_start=window_start,
                window_end=window_start + timedelta(hours=entry.window_hours),
            )
        )
    return scheduled
