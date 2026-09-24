"""Campaign API schemas (API_PLAN §2.5): create/wizard, read models for the planning outputs."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omnigrowth.core.enums import (
    AngleType,
    CampaignObjective,
    CampaignStatus,
    ClaimLabel,
    ContentFormat,
    ContentIdeaStatus,
    FunnelStage,
    Platform,
    RoutingProfile,
)
from omnigrowth.schemas.brand import RequiredName, Timezone
from omnigrowth.schemas.sources import JobRead


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CampaignCreate(_Request):
    brand_id: uuid.UUID
    name: RequiredName
    objective: CampaignObjective
    topic: str = Field(min_length=3, max_length=2000)
    primary_platform: Platform
    secondary_platforms: list[Platform] = Field(default_factory=list, max_length=8)
    audience_id: uuid.UUID | None = None
    offer_id: uuid.UUID | None = None
    content_pillar: str | None = Field(default=None, max_length=200)
    priority: int = Field(default=3, ge=1, le=5)
    start_date: date | None = None
    end_date: date | None = None
    timezone: Timezone | None = None
    routing_profile: RoutingProfile | None = None
    budget_limit_usd: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=6)
    source_asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _consistent(self) -> CampaignCreate:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        self.secondary_platforms = [
            p for p in dict.fromkeys(self.secondary_platforms) if p != self.primary_platform
        ]
        if len(set(self.source_asset_ids)) != len(self.source_asset_ids):
            raise ValueError("source_asset_ids must be unique")
        return self


class CampaignWizardRequest(CampaignCreate):
    """§39 steps 1-8 in one call: optional inline source text, optional generation."""

    source_text: str | None = Field(default=None, min_length=1, max_length=200_000)
    source_title: str | None = Field(default=None, max_length=500)
    generate: bool = False


class CampaignRead(_Read):
    id: uuid.UUID
    brand_id: uuid.UUID
    name: str
    objective: CampaignObjective
    topic: str
    primary_platform: Platform
    secondary_platforms: list[Platform]
    audience_id: uuid.UUID | None
    offer_id: uuid.UUID | None
    content_pillar: str | None
    status: CampaignStatus
    priority: int
    start_date: date | None
    end_date: date | None
    timezone: str | None
    routing_profile: RoutingProfile
    budget_limit_usd: Decimal | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class CampaignWizardResultRead(BaseModel):
    campaign: CampaignRead
    source_asset_id: uuid.UUID | None
    job: JobRead | None


class CampaignStrategyRead(_Read):
    id: uuid.UUID
    campaign_id: uuid.UUID
    version_no: int
    is_active: bool
    agent_run_id: uuid.UUID | None
    strategy: dict[str, Any]
    positioning: str
    funnel_stage: FunnelStage
    primary_cta: str | None
    primary_platform: Platform
    secondary_platforms: list[Platform]
    created_at: datetime


class ResearchClaimRead(_Read):
    id: uuid.UUID
    ordinal: int
    claim_text: str
    label: ClaimLabel
    source_excerpt: str | None
    citation_url: str | None
    citation_retrieved_at: datetime | None


class ResearchPacketRead(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    source_asset_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    provider: str
    summary: str | None
    created_at: datetime
    claim_counts: dict[ClaimLabel, int]
    claims: list[ResearchClaimRead]


class ContentIdeaRead(_Read):
    id: uuid.UUID
    campaign_id: uuid.UUID
    agent_run_id: uuid.UUID | None
    angle: AngleType
    hook: str
    audience_id: uuid.UUID | None
    platform_targets: list[Platform]
    estimated_value: Decimal | None
    evidence: list[Any]
    status: ContentIdeaStatus


class PlanItemRead(_Read):
    id: uuid.UUID
    campaign_id: uuid.UUID
    agent_run_id: uuid.UUID | None
    horizon_days: int
    ordinal: int
    platform: Platform
    format: ContentFormat
    angle: AngleType
    objective: CampaignObjective
    funnel_stage: FunnelStage
    cta: str | None
    source_asset_id: uuid.UUID | None
    content_idea_id: uuid.UUID | None
    depends_on_plan_item_id: uuid.UUID | None
    stale: bool
    window_start: datetime
    window_end: datetime
