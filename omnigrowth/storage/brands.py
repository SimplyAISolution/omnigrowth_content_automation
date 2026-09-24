"""Persistence for brands, brand_profiles, audiences and offers (DATA_MODEL §3.3-§3.6).

Pure data access: no commits (services own the transaction), no authorization. Integrity
violations are translated into categorized ``OmniGrowthError``s here so callers never see
driver exceptions.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import CampaignStatus, PublicationJobStatus
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.models import Audience, Brand, BrandProfile, Campaign, Offer, PublicationJob

_UNIQUE_VIOLATION = "23505"
# Publication jobs that still represent pending/in-flight work for a brand.
_OPEN_JOB_STATUSES = (
    PublicationJobStatus.PENDING,
    PublicationJobStatus.CLAIMED,
    PublicationJobStatus.RETRY_WAIT,
    PublicationJobStatus.UNCERTAIN,
)


def _integrity_error(exc: IntegrityError, entity: str) -> OmniGrowthError:
    orig = exc.orig
    sqlstate = getattr(orig, "sqlstate", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
    details: dict[str, Any] = {"entity": entity}
    if constraint:
        details["constraint"] = str(constraint)
    if sqlstate == _UNIQUE_VIOLATION:
        return OmniGrowthError(ErrorCode.CONFLICT, f"{entity} already exists", details=details)
    return OmniGrowthError(
        ErrorCode.VALIDATION_FAILURE, f"{entity} violates a data constraint", details=details
    )


async def flush_row[Row: (Brand, BrandProfile, Audience, Offer)](
    session: AsyncSession, row: Row, entity: str
) -> Row:
    """Flush pending changes and reload server-generated columns (timestamps)."""
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _integrity_error(exc, entity) from exc
    await session.refresh(row)
    return row


# ---------------------------------------------------------------- brands
async def get_brand(
    session: AsyncSession,
    brand_id: uuid.UUID,
    *,
    include_archived: bool = False,
    for_update: bool = False,
) -> Brand | None:
    stmt = select(Brand).where(Brand.id == brand_id)
    if not include_archived:
        stmt = stmt.where(Brand.archived_at.is_(None))
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_brands(
    session: AsyncSession, *, include_archived: bool, before_id: uuid.UUID | None, limit: int
) -> Sequence[Brand]:
    stmt = select(Brand)
    if not include_archived:
        stmt = stmt.where(Brand.archived_at.is_(None))
    if before_id is not None:
        stmt = stmt.where(Brand.id < before_id)
    stmt = stmt.order_by(Brand.id.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()


async def add_brand(session: AsyncSession, brand: Brand) -> Brand:
    session.add(brand)
    return await flush_row(session, brand, "brand")


async def brand_has_open_work(session: AsyncSession, brand_id: uuid.UUID) -> dict[str, int]:
    """Counts that block archiving a brand (API_PLAN §2.3): ACTIVE campaigns, open jobs."""
    campaigns = (
        await session.execute(
            select(func.count())
            .select_from(Campaign)
            .where(
                Campaign.brand_id == brand_id,
                Campaign.status == CampaignStatus.ACTIVE,
                Campaign.archived_at.is_(None),
            )
        )
    ).scalar_one()
    jobs = (
        await session.execute(
            select(func.count())
            .select_from(PublicationJob)
            .where(
                PublicationJob.brand_id == brand_id,
                PublicationJob.status.in_(_OPEN_JOB_STATUSES),
            )
        )
    ).scalar_one()
    return {"active_campaigns": int(campaigns), "open_publication_jobs": int(jobs)}


# ---------------------------------------------------------------- brand profiles
async def get_active_profile(session: AsyncSession, brand_id: uuid.UUID) -> BrandProfile | None:
    stmt = select(BrandProfile).where(
        BrandProfile.brand_id == brand_id, BrandProfile.is_active.is_(True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def max_profile_version(session: AsyncSession, brand_id: uuid.UUID) -> int:
    stmt = select(func.coalesce(func.max(BrandProfile.version_no), 0)).where(
        BrandProfile.brand_id == brand_id
    )
    return int((await session.execute(stmt)).scalar_one())


async def list_profiles(
    session: AsyncSession, brand_id: uuid.UUID, *, before_version: int | None, limit: int
) -> Sequence[BrandProfile]:
    stmt = select(BrandProfile).where(BrandProfile.brand_id == brand_id)
    if before_version is not None:
        stmt = stmt.where(BrandProfile.version_no < before_version)
    stmt = stmt.order_by(BrandProfile.version_no.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()


async def deactivate_profiles(session: AsyncSession, brand_id: uuid.UUID) -> None:
    """Flip the active version off. Must run (and flush) before the new active insert so the
    partial unique index ``brand_profiles_one_active_uq`` is never violated."""
    await session.execute(
        update(BrandProfile)
        .where(BrandProfile.brand_id == brand_id, BrandProfile.is_active.is_(True))
        .values(is_active=False)
        .execution_options(synchronize_session="fetch")
    )


async def add_profile(session: AsyncSession, profile: BrandProfile) -> BrandProfile:
    session.add(profile)
    return await flush_row(session, profile, "brand_profile")


# ---------------------------------------------------------------- audiences
async def get_audience(
    session: AsyncSession,
    brand_id: uuid.UUID,
    audience_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> Audience | None:
    stmt = select(Audience).where(Audience.id == audience_id, Audience.brand_id == brand_id)
    if not include_archived:
        stmt = stmt.where(Audience.archived_at.is_(None))
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_audiences(
    session: AsyncSession,
    brand_id: uuid.UUID,
    *,
    include_archived: bool,
    before_id: uuid.UUID | None,
    limit: int,
) -> Sequence[Audience]:
    stmt = select(Audience).where(Audience.brand_id == brand_id)
    if not include_archived:
        stmt = stmt.where(Audience.archived_at.is_(None))
    if before_id is not None:
        stmt = stmt.where(Audience.id < before_id)
    stmt = stmt.order_by(Audience.id.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()


async def unset_primary_audiences(
    session: AsyncSession, brand_id: uuid.UUID, *, except_id: uuid.UUID | None
) -> list[uuid.UUID]:
    """Demote the current (non-archived) primary audience(s); returns the demoted ids."""
    stmt = (
        update(Audience)
        .where(
            Audience.brand_id == brand_id,
            Audience.is_primary.is_(True),
            Audience.archived_at.is_(None),
        )
        .values(is_primary=False)
        .returning(Audience.id)
        .execution_options(synchronize_session="fetch")
    )
    if except_id is not None:
        stmt = stmt.where(Audience.id != except_id)
    return list((await session.execute(stmt)).scalars().all())


async def has_other_primary(
    session: AsyncSession, brand_id: uuid.UUID, audience_id: uuid.UUID
) -> bool:
    stmt = select(func.count()).where(
        Audience.brand_id == brand_id,
        Audience.id != audience_id,
        Audience.is_primary.is_(True),
        Audience.archived_at.is_(None),
    )
    return int((await session.execute(stmt)).scalar_one()) > 0


async def audience_active_campaigns(session: AsyncSession, audience_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Campaign)
        .where(
            Campaign.audience_id == audience_id,
            Campaign.status == CampaignStatus.ACTIVE,
            Campaign.archived_at.is_(None),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def add_audience(session: AsyncSession, audience: Audience) -> Audience:
    session.add(audience)
    return await flush_row(session, audience, "audience")


# ---------------------------------------------------------------- offers
async def get_offer(
    session: AsyncSession,
    brand_id: uuid.UUID,
    offer_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> Offer | None:
    stmt = select(Offer).where(Offer.id == offer_id, Offer.brand_id == brand_id)
    if not include_archived:
        stmt = stmt.where(Offer.archived_at.is_(None))
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_offers(
    session: AsyncSession,
    brand_id: uuid.UUID,
    *,
    include_archived: bool,
    before_id: uuid.UUID | None,
    limit: int,
) -> Sequence[Offer]:
    stmt = select(Offer).where(Offer.brand_id == brand_id)
    if not include_archived:
        stmt = stmt.where(Offer.archived_at.is_(None))
    if before_id is not None:
        stmt = stmt.where(Offer.id < before_id)
    stmt = stmt.order_by(Offer.id.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()


async def add_offer(session: AsyncSession, offer: Offer) -> Offer:
    session.add(offer)
    return await flush_row(session, offer, "offer")
