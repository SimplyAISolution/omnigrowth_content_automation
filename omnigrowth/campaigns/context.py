"""Planning context: everything the planning agents may see about a campaign, as text.

Rules (keep FakeProvider fixture hashes stable, A8):
* no ids, timestamps or other run-specific values in rendered blocks;
* deterministic ordering and formatting;
* bounded size: the source excerpt is capped at ``SOURCE_CHAR_LIMIT`` characters.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import (
    CampaignObjective,
    Platform,
    RoutingProfile,
    SourceAssetStatus,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.models import (
    Audience,
    Brand,
    BrandProfile,
    Campaign,
    CampaignSource,
    Offer,
    SourceAsset,
)

SOURCE_CHAR_LIMIT = 12_000


@dataclass(frozen=True, slots=True)
class PlanningContext:
    brand_id: uuid.UUID
    campaign_id: uuid.UUID
    brand_profile_id: uuid.UUID | None
    audience_id: uuid.UUID | None
    source_asset_id: uuid.UUID
    routing_profile: RoutingProfile
    timezone: str
    start_date: date | None
    objective: CampaignObjective
    primary_platform: Platform
    secondary_platforms: tuple[Platform, ...]
    brand_block: str
    campaign_block: str
    source_text: str

    @property
    def platforms(self) -> tuple[Platform, ...]:
        return (self.primary_platform, *self.secondary_platforms)


def _lines(pairs: Iterable[tuple[str, object]]) -> str:
    out: list[str] = []
    for label, value in pairs:
        if value is None or value == "" or value == [] or value == ():
            continue
        if isinstance(value, list | tuple):
            value = "; ".join(str(v) for v in value)
        out.append(f"{label}: {value}")
    return "\n".join(out) if out else "(none)"


def _items(values: list[object] | None) -> list[str]:
    """Profile JSONB lists hold strings or small objects; render both compactly."""
    rendered: list[str] = []
    for value in values or []:
        if isinstance(value, dict):
            rendered.append(", ".join(f"{k}={v}" for k, v in sorted(value.items())))
        else:
            rendered.append(str(value))
    return rendered


def _brand_block(
    brand: Brand, profile: BrandProfile | None, audience: Audience | None, offer: Offer | None
) -> str:
    parts: list[tuple[str, object]] = [("Brand", brand.display_name)]
    if profile is not None:
        parts += [
            ("Company", profile.company_description),
            ("Niche", profile.niche),
            ("Mission", profile.mission),
            ("Products", _items(profile.products)),
            ("Services", _items(profile.services)),
            ("Differentiators", _items(profile.differentiators)),
            ("Content pillars", list(profile.content_pillars)),
            ("Avoid", list(profile.avoid_list)),
            ("Banned phrases", list(profile.banned_phrases)),
        ]
    if audience is not None:
        parts += [
            ("Audience", audience.name),
            ("Audience description", audience.description),
            ("Audience pains", list(audience.pains)),
            ("Audience goals", list(audience.goals)),
            ("Audience objections", list(audience.objections)),
            ("Audience questions", list(audience.common_questions)),
        ]
    if offer is not None:
        parts += [("Offer", offer.name), ("Offer CTA", offer.cta)]
    return _lines(parts)


def _campaign_block(campaign: Campaign) -> str:
    return _lines(
        [
            ("Campaign", campaign.name),
            ("Objective", campaign.objective.value),
            ("Topic", campaign.topic),
            ("Content pillar", campaign.content_pillar),
            ("Primary platform", campaign.primary_platform.value),
            ("Secondary platforms", [p.value for p in campaign.secondary_platforms]),
        ]
    )


async def load_planning_context(session: AsyncSession, campaign_id: uuid.UUID) -> PlanningContext:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "campaign not found")
    brand = await session.get(Brand, campaign.brand_id)
    if brand is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "brand not found")
    profile = (
        await session.execute(
            select(BrandProfile).where(
                BrandProfile.brand_id == brand.id, BrandProfile.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    if campaign.audience_id is not None:
        audience = await session.get(Audience, campaign.audience_id)
    else:
        audience = (
            await session.execute(
                select(Audience).where(
                    Audience.brand_id == brand.id,
                    Audience.is_primary.is_(True),
                    Audience.archived_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    offer = await session.get(Offer, campaign.offer_id) if campaign.offer_id else None

    sources = (
        await session.execute(
            select(SourceAsset)
            .join(CampaignSource, CampaignSource.source_asset_id == SourceAsset.id)
            .where(
                CampaignSource.campaign_id == campaign_id,
                SourceAsset.status == SourceAssetStatus.NORMALIZED,
            )
            .order_by(CampaignSource.position)
        )
    ).scalars()
    texts: list[str] = []
    first_source: uuid.UUID | None = None
    for source in sources:
        if not source.normalized_content:
            continue
        first_source = first_source or source.id
        header = f"# {source.title}\n\n" if source.title else ""
        texts.append(header + source.normalized_content.strip())
    if first_source is None:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "campaign has no normalized source to plan from",
            details={"failed_guard": "has_source"},
        )
    source_text = "\n\n---\n\n".join(texts)[:SOURCE_CHAR_LIMIT]

    return PlanningContext(
        brand_id=brand.id,
        campaign_id=campaign.id,
        brand_profile_id=profile.id if profile else None,
        audience_id=audience.id if audience else None,
        source_asset_id=first_source,
        routing_profile=campaign.routing_profile,
        timezone=campaign.timezone or brand.timezone,
        start_date=campaign.start_date,
        objective=campaign.objective,
        primary_platform=campaign.primary_platform,
        secondary_platforms=tuple(campaign.secondary_platforms),
        brand_block=_brand_block(brand, profile, audience, offer),
        campaign_block=_campaign_block(campaign),
        source_text=source_text,
    )
