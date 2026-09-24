"""``/api/v1/brands`` — brands, append-only brand profile versions, audiences, offers
(spec §5, API_PLAN §2.3). Thin HTTP layer: parsing, roles and status codes only."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status

from omnigrowth.api.deps import EditorDep, OwnerDep, SessionDep, ViewerDep
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.schemas.brand import (
    AudienceCreate,
    AudienceRead,
    AudienceUpdate,
    BrandCreate,
    BrandProfileRead,
    BrandProfileUpdate,
    BrandRead,
    BrandUpdate,
    OfferCreate,
    OfferRead,
    OfferUpdate,
)
from omnigrowth.schemas.common import Page
from omnigrowth.services import brands as service

router = APIRouter(prefix="/brands", tags=["brands"])

Limit = Annotated[int, Query(ge=1, le=200)]
Cursor = Annotated[str | None, Query(max_length=512)]
IncludeArchived = Annotated[bool, Query()]

_BASE = "/api/v1/brands"


def _parse_if_match(value: str | None) -> int | None:
    """``If-Match: "3"`` (or ``W/"3"`` / ``3``) -> expected profile version_no."""
    if value is None:
        return None
    raw = value.strip()
    if raw.startswith("W/"):
        raw = raw[2:]
    raw = raw.strip('"')
    if not raw.isdigit() or int(raw) < 1:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "If-Match must be a profile version number",
            details={"reason": "INVALID_IF_MATCH"},
        )
    return int(raw)


# ---------------------------------------------------------------- brands
@router.post("", response_model=BrandRead, status_code=status.HTTP_201_CREATED)
async def create_brand(
    body: BrandCreate, principal: EditorDep, session: SessionDep, response: Response
) -> BrandRead:
    brand = await service.create_brand(session, principal.actor, body)
    response.headers["Location"] = f"{_BASE}/{brand.id}"
    return brand


@router.get("", response_model=Page[BrandRead])
async def list_brands(
    _principal: ViewerDep,
    session: SessionDep,
    include_archived: IncludeArchived = False,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[BrandRead]:
    return await service.list_brands(
        session, include_archived=include_archived, cursor=cursor, limit=limit
    )


@router.get("/{brand_id}", response_model=BrandRead)
async def get_brand(
    brand_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    include_archived: IncludeArchived = False,
) -> BrandRead:
    return await service.get_brand(session, brand_id, include_archived=include_archived)


@router.patch("/{brand_id}", response_model=BrandRead)
async def update_brand(
    brand_id: uuid.UUID, body: BrandUpdate, principal: EditorDep, session: SessionDep
) -> BrandRead:
    return await service.update_brand(session, principal.actor, brand_id, body)


@router.post("/{brand_id}/archive", response_model=BrandRead)
async def archive_brand(brand_id: uuid.UUID, principal: OwnerDep, session: SessionDep) -> BrandRead:
    return await service.archive_brand(session, principal.actor, brand_id)


@router.post("/{brand_id}/restore", response_model=BrandRead)
async def restore_brand(brand_id: uuid.UUID, principal: OwnerDep, session: SessionDep) -> BrandRead:
    return await service.restore_brand(session, principal.actor, brand_id)


# ---------------------------------------------------------------- brand profile
@router.get("/{brand_id}/profile", response_model=BrandProfileRead)
async def get_profile(
    brand_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> BrandProfileRead:
    return await service.get_active_profile(session, brand_id)


@router.get("/{brand_id}/profile/versions", response_model=Page[BrandProfileRead])
async def list_profile_versions(
    brand_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[BrandProfileRead]:
    return await service.list_profile_versions(session, brand_id, cursor=cursor, limit=limit)


@router.put(
    "/{brand_id}/profile", response_model=BrandProfileRead, status_code=status.HTTP_201_CREATED
)
async def put_profile(
    brand_id: uuid.UUID,
    body: BrandProfileUpdate,
    principal: EditorDep,
    session: SessionDep,
    response: Response,
    if_match: Annotated[str | None, Header(max_length=64)] = None,
) -> BrandProfileRead:
    """Append a new profile version (§20: never an in-place rewrite)."""
    profile = await service.create_profile_version(
        session, principal.actor, brand_id, body, if_match_version=_parse_if_match(if_match)
    )
    response.headers["Location"] = f"{_BASE}/{brand_id}/profile/versions"
    response.headers["ETag"] = f'"{profile.version_no}"'
    return profile


# ---------------------------------------------------------------- audiences
@router.post(
    "/{brand_id}/audiences", response_model=AudienceRead, status_code=status.HTTP_201_CREATED
)
async def create_audience(
    brand_id: uuid.UUID,
    body: AudienceCreate,
    principal: EditorDep,
    session: SessionDep,
    response: Response,
) -> AudienceRead:
    audience = await service.create_audience(session, principal.actor, brand_id, body)
    response.headers["Location"] = f"{_BASE}/{brand_id}/audiences/{audience.id}"
    return audience


@router.get("/{brand_id}/audiences", response_model=Page[AudienceRead])
async def list_audiences(
    brand_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    include_archived: IncludeArchived = False,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[AudienceRead]:
    return await service.list_audiences(
        session, brand_id, include_archived=include_archived, cursor=cursor, limit=limit
    )


@router.get("/{brand_id}/audiences/{audience_id}", response_model=AudienceRead)
async def get_audience(
    brand_id: uuid.UUID,
    audience_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    include_archived: IncludeArchived = False,
) -> AudienceRead:
    return await service.get_audience(
        session, brand_id, audience_id, include_archived=include_archived
    )


@router.patch("/{brand_id}/audiences/{audience_id}", response_model=AudienceRead)
async def update_audience(
    brand_id: uuid.UUID,
    audience_id: uuid.UUID,
    body: AudienceUpdate,
    principal: EditorDep,
    session: SessionDep,
) -> AudienceRead:
    return await service.update_audience(session, principal.actor, brand_id, audience_id, body)


@router.post("/{brand_id}/audiences/{audience_id}/archive", response_model=AudienceRead)
async def archive_audience(
    brand_id: uuid.UUID, audience_id: uuid.UUID, principal: EditorDep, session: SessionDep
) -> AudienceRead:
    return await service.archive_audience(session, principal.actor, brand_id, audience_id)


@router.post("/{brand_id}/audiences/{audience_id}/restore", response_model=AudienceRead)
async def restore_audience(
    brand_id: uuid.UUID, audience_id: uuid.UUID, principal: EditorDep, session: SessionDep
) -> AudienceRead:
    return await service.restore_audience(session, principal.actor, brand_id, audience_id)


# ---------------------------------------------------------------- offers
@router.post("/{brand_id}/offers", response_model=OfferRead, status_code=status.HTTP_201_CREATED)
async def create_offer(
    brand_id: uuid.UUID,
    body: OfferCreate,
    principal: EditorDep,
    session: SessionDep,
    response: Response,
) -> OfferRead:
    offer = await service.create_offer(session, principal.actor, brand_id, body)
    response.headers["Location"] = f"{_BASE}/{brand_id}/offers/{offer.id}"
    return offer


@router.get("/{brand_id}/offers", response_model=Page[OfferRead])
async def list_offers(
    brand_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    include_archived: IncludeArchived = False,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[OfferRead]:
    return await service.list_offers(
        session, brand_id, include_archived=include_archived, cursor=cursor, limit=limit
    )


@router.get("/{brand_id}/offers/{offer_id}", response_model=OfferRead)
async def get_offer(
    brand_id: uuid.UUID,
    offer_id: uuid.UUID,
    _principal: ViewerDep,
    session: SessionDep,
    include_archived: IncludeArchived = False,
) -> OfferRead:
    return await service.get_offer(session, brand_id, offer_id, include_archived=include_archived)


@router.patch("/{brand_id}/offers/{offer_id}", response_model=OfferRead)
async def update_offer(
    brand_id: uuid.UUID,
    offer_id: uuid.UUID,
    body: OfferUpdate,
    principal: EditorDep,
    session: SessionDep,
) -> OfferRead:
    return await service.update_offer(session, principal.actor, brand_id, offer_id, body)


@router.post("/{brand_id}/offers/{offer_id}/archive", response_model=OfferRead)
async def archive_offer(
    brand_id: uuid.UUID, offer_id: uuid.UUID, principal: EditorDep, session: SessionDep
) -> OfferRead:
    return await service.archive_offer(session, principal.actor, brand_id, offer_id)


@router.post("/{brand_id}/offers/{offer_id}/restore", response_model=OfferRead)
async def restore_offer(
    brand_id: uuid.UUID, offer_id: uuid.UUID, principal: EditorDep, session: SessionDep
) -> OfferRead:
    return await service.restore_offer(session, principal.actor, brand_id, offer_id)
