"""Brand use-cases (spec §5, API_PLAN §2.3): brands, append-only profile versions,
audiences and offers.

Every mutating use-case writes an ``audit_events`` row via ``record_audit`` in the same
transaction and commits explicitly. §20: the profile is never rewritten in place; each
change is a new version created by an explicit actor.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import ActorType, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.state_machines import Actor
from omnigrowth.models import Audience, Brand, BrandProfile, Offer
from omnigrowth.observability.audit import record_audit
from omnigrowth.schemas.brand import (
    PROFILE_CONTENT_FIELDS,
    AudienceCreate,
    AudienceRead,
    AudienceUpdate,
    BrandCreate,
    BrandProfileCreate,
    BrandProfileRead,
    BrandProfileUpdate,
    BrandRead,
    BrandUpdate,
    OfferCreate,
    OfferRead,
    OfferUpdate,
)
from omnigrowth.schemas.common import Page
from omnigrowth.storage import brands as repo


# ---------------------------------------------------------------- cursors (API_PLAN §1.3)
def encode_cursor(key: str, scope: str) -> str:
    raw = json.dumps({"k": key, "s": scope}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, scope: str) -> str:
    """Return the cursor key. Malformed, or issued for other filters -> 422."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise _bad_cursor() from exc
    if not isinstance(data, dict) or data.get("s") != scope or not isinstance(data.get("k"), str):
        raise _bad_cursor()
    return str(data["k"])


def _bad_cursor() -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.VALIDATION_FAILURE, "invalid cursor", details={"reason": "INVALID_CURSOR"}
    )


def _uuid_cursor(cursor: str | None, scope: str) -> uuid.UUID | None:
    if cursor is None:
        return None
    try:
        return uuid.UUID(decode_cursor(cursor, scope))
    except ValueError as exc:
        raise _bad_cursor() from exc


def _page[RowT, ReadT: BaseModel](
    rows: Sequence[RowT],
    limit: int,
    to_read: Callable[[RowT], ReadT],
    key: Callable[[RowT], str],
    scope: str,
) -> Page[ReadT]:
    items = list(rows[:limit])
    next_cursor = encode_cursor(key(items[-1]), scope) if len(rows) > limit and items else None
    return Page[ReadT](items=[to_read(r) for r in items], next_cursor=next_cursor)


# ---------------------------------------------------------------- helpers
def _not_found(entity: str, entity_id: uuid.UUID) -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.NOT_FOUND, f"{entity} not found", details={"entity": entity, "id": str(entity_id)}
    )


def _snapshot(model: BaseModel, fields: Sequence[str] | None = None) -> dict[str, Any]:
    data = model.model_dump(mode="json")
    if fields is None:
        return data
    return {f: data[f] for f in fields if f in data}


async def _audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    brand_id: uuid.UUID,
    *,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    await record_audit(
        session,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        brand_id=brand_id,
        before=before,
        after=after,
        metadata=metadata,
    )


async def _require_brand(
    session: AsyncSession, brand_id: uuid.UUID, *, for_update: bool = False
) -> Brand:
    brand = await repo.get_brand(session, brand_id, for_update=for_update)
    if brand is None:
        raise _not_found("brand", brand_id)
    return brand


def _brand_read(brand: Brand) -> BrandRead:
    return BrandRead.model_validate(brand)


def _profile_read(profile: BrandProfile) -> BrandProfileRead:
    return BrandProfileRead.model_validate(profile)


def _audience_read(audience: Audience) -> AudienceRead:
    return AudienceRead.model_validate(audience)


def _offer_read(offer: Offer) -> OfferRead:
    return OfferRead.model_validate(offer)


# ---------------------------------------------------------------- brands
async def create_brand(session: AsyncSession, actor: Actor, body: BrandCreate) -> BrandRead:
    """Create a brand and its profile v1 (active); an empty profile when none is given."""
    brand = await repo.add_brand(
        session,
        Brand(
            id=uuid7(),
            slug=body.slug,
            name=body.name,
            display_name=body.display_name or body.name,
            timezone=body.timezone,
            default_routing_profile=body.default_routing_profile,
            auto_approve_enabled=False,
        ),
    )
    read = _brand_read(brand)
    await _audit(
        session, actor, "brand.create", "brands", brand.id, brand.id, after=_snapshot(read)
    )
    profile = await _insert_profile_version(
        session, actor, brand.id, body.profile or BrandProfileCreate(), version_no=1
    )
    await _audit(
        session,
        actor,
        "brand_profile.version.create",
        "brand_profiles",
        profile.id,
        brand.id,
        after=_snapshot(_profile_read(profile)),
        metadata={"version_no": 1},
    )
    await session.commit()
    return read


async def list_brands(
    session: AsyncSession, *, include_archived: bool, cursor: str | None, limit: int
) -> Page[BrandRead]:
    scope = f"brands:{int(include_archived)}"
    rows = await repo.list_brands(
        session,
        include_archived=include_archived,
        before_id=_uuid_cursor(cursor, scope),
        limit=limit + 1,
    )
    return _page(rows, limit, _brand_read, lambda b: str(b.id), scope)


async def get_brand(
    session: AsyncSession, brand_id: uuid.UUID, *, include_archived: bool = False
) -> BrandRead:
    brand = await repo.get_brand(session, brand_id, include_archived=include_archived)
    if brand is None:
        raise _not_found("brand", brand_id)
    return _brand_read(brand)


async def update_brand(
    session: AsyncSession, actor: Actor, brand_id: uuid.UUID, body: BrandUpdate
) -> BrandRead:
    changes = body.model_dump(exclude_unset=True)
    if "auto_approve_enabled" in changes and actor.role is not UserRole.OWNER:
        raise OmniGrowthError(
            ErrorCode.FORBIDDEN,
            "only an OWNER may change auto_approve_enabled",
            details={"field": "auto_approve_enabled"},
        )
    brand = await _require_brand(session, brand_id, for_update=True)
    before = _snapshot(_brand_read(brand), list(changes))
    for field, value in changes.items():
        setattr(brand, field, value)
    brand = await repo.flush_row(session, brand, "brand")
    read = _brand_read(brand)
    if changes:
        await _audit(
            session,
            actor,
            "brand.update",
            "brands",
            brand.id,
            brand.id,
            before=before,
            after=_snapshot(read, list(changes)),
        )
    await session.commit()
    return read


async def archive_brand(session: AsyncSession, actor: Actor, brand_id: uuid.UUID) -> BrandRead:
    brand = await repo.get_brand(session, brand_id, include_archived=True, for_update=True)
    if brand is None:
        raise _not_found("brand", brand_id)
    if brand.archived_at is not None:
        raise OmniGrowthError(ErrorCode.CONFLICT, "brand is already archived")
    blockers = await repo.brand_has_open_work(session, brand_id)
    if any(blockers.values()):
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "brand has active campaigns or open publication jobs",
            details=blockers,
        )
    brand.archived_at = datetime.now(UTC)
    brand = await repo.flush_row(session, brand, "brand")
    read = _brand_read(brand)
    await _audit(
        session,
        actor,
        "brand.archive",
        "brands",
        brand.id,
        brand.id,
        before={"archived_at": None},
        after=_snapshot(read, ["archived_at"]),
    )
    await session.commit()
    return read


async def restore_brand(session: AsyncSession, actor: Actor, brand_id: uuid.UUID) -> BrandRead:
    brand = await repo.get_brand(session, brand_id, include_archived=True, for_update=True)
    if brand is None:
        raise _not_found("brand", brand_id)
    if brand.archived_at is None:
        raise OmniGrowthError(ErrorCode.CONFLICT, "brand is not archived")
    before = _snapshot(_brand_read(brand), ["archived_at"])
    brand.archived_at = None
    brand = await repo.flush_row(session, brand, "brand")
    read = _brand_read(brand)
    await _audit(
        session,
        actor,
        "brand.restore",
        "brands",
        brand.id,
        brand.id,
        before=before,
        after={"archived_at": None},
    )
    await session.commit()
    return read


# ---------------------------------------------------------------- brand profiles
async def _insert_profile_version(
    session: AsyncSession,
    actor: Actor,
    brand_id: uuid.UUID,
    content: BrandProfileCreate,
    *,
    version_no: int,
) -> BrandProfile:
    values = content.model_dump(include=set(PROFILE_CONTENT_FIELDS))
    return await repo.add_profile(
        session,
        BrandProfile(
            id=uuid7(),
            brand_id=brand_id,
            version_no=version_no,
            is_active=True,
            actor_type=ActorType(actor.type),
            actor_id=actor.id,
            **values,
        ),
    )


async def get_active_profile(session: AsyncSession, brand_id: uuid.UUID) -> BrandProfileRead:
    await _require_brand(session, brand_id)
    profile = await repo.get_active_profile(session, brand_id)
    if profile is None:
        raise OmniGrowthError(
            ErrorCode.NOT_FOUND, "brand has no active profile", details={"brand_id": str(brand_id)}
        )
    return _profile_read(profile)


async def list_profile_versions(
    session: AsyncSession, brand_id: uuid.UUID, *, cursor: str | None, limit: int
) -> Page[BrandProfileRead]:
    await _require_brand(session, brand_id)
    scope = f"brand_profiles:{brand_id}"
    before: int | None = None
    if cursor is not None:
        key = decode_cursor(cursor, scope)
        if not key.isdigit():
            raise _bad_cursor()
        before = int(key)
    rows = await repo.list_profiles(session, brand_id, before_version=before, limit=limit + 1)
    return _page(rows, limit, _profile_read, lambda p: str(p.version_no), scope)


async def create_profile_version(
    session: AsyncSession,
    actor: Actor,
    brand_id: uuid.UUID,
    body: BrandProfileUpdate,
    *,
    if_match_version: int | None = None,
) -> BrandProfileRead:
    """Append version max+1 and make it the only active one (one transaction).

    The brand row is locked ``FOR UPDATE`` so concurrent writers serialize; existing
    versions are never modified except for the ``is_active`` flag.
    """
    await _require_brand(session, brand_id, for_update=True)
    current = await repo.get_active_profile(session, brand_id)
    latest = await repo.max_profile_version(session, brand_id)
    expected = (
        body.expected_version_no if body.expected_version_no is not None else if_match_version
    )
    if (
        body.expected_version_no is not None
        and if_match_version is not None
        and body.expected_version_no != if_match_version
    ):
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "If-Match and expected_version_no disagree",
            details={"reason": "PRECONDITION_MISMATCH"},
        )
    if expected is not None and expected != latest:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "brand profile was changed by someone else",
            details={"expected_version_no": expected, "current_version_no": latest},
        )
    await repo.deactivate_profiles(session, brand_id)
    profile = await _insert_profile_version(session, actor, brand_id, body, version_no=latest + 1)
    read = _profile_read(profile)
    await _audit(
        session,
        actor,
        "brand_profile.version.create",
        "brand_profiles",
        profile.id,
        brand_id,
        before=_snapshot(_profile_read(current)) if current is not None else None,
        after=_snapshot(read),
        metadata={
            "version_no": profile.version_no,
            "previous_version_id": str(current.id) if current is not None else None,
        },
    )
    await session.commit()
    return read


# ---------------------------------------------------------------- audiences
async def _require_audience(
    session: AsyncSession, brand_id: uuid.UUID, audience_id: uuid.UUID, **kw: bool
) -> Audience:
    audience = await repo.get_audience(session, brand_id, audience_id, **kw)
    if audience is None:
        raise _not_found("audience", audience_id)
    return audience


async def _demote_other_primaries(
    session: AsyncSession, actor: Actor, brand_id: uuid.UUID, except_id: uuid.UUID
) -> None:
    for demoted in await repo.unset_primary_audiences(session, brand_id, except_id=except_id):
        await _audit(
            session,
            actor,
            "audience.update",
            "audiences",
            demoted,
            brand_id,
            before={"is_primary": True},
            after={"is_primary": False},
            metadata={"reason": "PRIMARY_REASSIGNED", "new_primary_id": str(except_id)},
        )


async def create_audience(
    session: AsyncSession, actor: Actor, brand_id: uuid.UUID, body: AudienceCreate
) -> AudienceRead:
    await _require_brand(session, brand_id, for_update=True)
    audience_id = uuid7()
    if body.is_primary:
        # Before the insert: the partial unique index allows one primary per brand.
        await _demote_other_primaries(session, actor, brand_id, audience_id)
    audience = await repo.add_audience(
        session, Audience(id=audience_id, brand_id=brand_id, **body.model_dump())
    )
    read = _audience_read(audience)
    await _audit(
        session, actor, "audience.create", "audiences", audience.id, brand_id, after=_snapshot(read)
    )
    await session.commit()
    return read


async def list_audiences(
    session: AsyncSession,
    brand_id: uuid.UUID,
    *,
    include_archived: bool,
    cursor: str | None,
    limit: int,
) -> Page[AudienceRead]:
    await _require_brand(session, brand_id)
    scope = f"audiences:{brand_id}:{int(include_archived)}"
    rows = await repo.list_audiences(
        session,
        brand_id,
        include_archived=include_archived,
        before_id=_uuid_cursor(cursor, scope),
        limit=limit + 1,
    )
    return _page(rows, limit, _audience_read, lambda a: str(a.id), scope)


async def get_audience(
    session: AsyncSession,
    brand_id: uuid.UUID,
    audience_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> AudienceRead:
    await _require_brand(session, brand_id)
    return _audience_read(
        await _require_audience(session, brand_id, audience_id, include_archived=include_archived)
    )


async def update_audience(
    session: AsyncSession,
    actor: Actor,
    brand_id: uuid.UUID,
    audience_id: uuid.UUID,
    body: AudienceUpdate,
) -> AudienceRead:
    await _require_brand(session, brand_id, for_update=True)
    audience = await _require_audience(session, brand_id, audience_id)
    changes = body.model_dump(exclude_unset=True)
    before = _snapshot(_audience_read(audience), list(changes))
    if changes.get("is_primary") is True and not audience.is_primary:
        await _demote_other_primaries(session, actor, brand_id, audience.id)
    for field, value in changes.items():
        setattr(audience, field, value)
    audience = await repo.flush_row(session, audience, "audience")
    read = _audience_read(audience)
    if changes:
        await _audit(
            session,
            actor,
            "audience.update",
            "audiences",
            audience.id,
            brand_id,
            before=before,
            after=_snapshot(read, list(changes)),
        )
    await session.commit()
    return read


async def archive_audience(
    session: AsyncSession, actor: Actor, brand_id: uuid.UUID, audience_id: uuid.UUID
) -> AudienceRead:
    await _require_brand(session, brand_id, for_update=True)
    audience = await _require_audience(session, brand_id, audience_id, include_archived=True)
    if audience.archived_at is not None:
        raise OmniGrowthError(ErrorCode.CONFLICT, "audience is already archived")
    active = await repo.audience_active_campaigns(session, audience.id)
    if active:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "audience is referenced by an ACTIVE campaign",
            details={"active_campaigns": active},
        )
    audience.archived_at = datetime.now(UTC)
    audience = await repo.flush_row(session, audience, "audience")
    read = _audience_read(audience)
    await _audit(
        session,
        actor,
        "audience.archive",
        "audiences",
        audience.id,
        brand_id,
        before={"archived_at": None},
        after=_snapshot(read, ["archived_at"]),
    )
    await session.commit()
    return read


async def restore_audience(
    session: AsyncSession, actor: Actor, brand_id: uuid.UUID, audience_id: uuid.UUID
) -> AudienceRead:
    await _require_brand(session, brand_id, for_update=True)
    audience = await _require_audience(session, brand_id, audience_id, include_archived=True)
    if audience.archived_at is None:
        raise OmniGrowthError(ErrorCode.CONFLICT, "audience is not archived")
    before = _snapshot(_audience_read(audience), ["archived_at", "is_primary"])
    demoted = False
    if audience.is_primary and await repo.has_other_primary(session, brand_id, audience.id):
        # Keep the brand's current primary; the restored audience comes back as secondary.
        audience.is_primary = False
        demoted = True
    audience.archived_at = None
    audience = await repo.flush_row(session, audience, "audience")
    read = _audience_read(audience)
    await _audit(
        session,
        actor,
        "audience.restore",
        "audiences",
        audience.id,
        brand_id,
        before=before,
        after=_snapshot(read, ["archived_at", "is_primary"]),
        metadata={"primary_demoted": demoted},
    )
    await session.commit()
    return read


# ---------------------------------------------------------------- offers
async def _check_offer_audience(
    session: AsyncSession, brand_id: uuid.UUID, audience_id: uuid.UUID | None
) -> None:
    if audience_id is None:
        return
    if await repo.get_audience(session, brand_id, audience_id) is None:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "audience_id does not reference an active audience of this brand",
            details={"field": "audience_id", "reason": "AUDIENCE_NOT_IN_BRAND"},
        )


def _offer_values(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("funnel_destination_url") is not None:
        data["funnel_destination_url"] = str(data["funnel_destination_url"])
    return data


async def create_offer(
    session: AsyncSession, actor: Actor, brand_id: uuid.UUID, body: OfferCreate
) -> OfferRead:
    await _require_brand(session, brand_id, for_update=True)
    await _check_offer_audience(session, brand_id, body.audience_id)
    offer = await repo.add_offer(
        session, Offer(id=uuid7(), brand_id=brand_id, **_offer_values(body.model_dump()))
    )
    read = _offer_read(offer)
    await _audit(
        session, actor, "offer.create", "offers", offer.id, brand_id, after=_snapshot(read)
    )
    await session.commit()
    return read


async def list_offers(
    session: AsyncSession,
    brand_id: uuid.UUID,
    *,
    include_archived: bool,
    cursor: str | None,
    limit: int,
) -> Page[OfferRead]:
    await _require_brand(session, brand_id)
    scope = f"offers:{brand_id}:{int(include_archived)}"
    rows = await repo.list_offers(
        session,
        brand_id,
        include_archived=include_archived,
        before_id=_uuid_cursor(cursor, scope),
        limit=limit + 1,
    )
    return _page(rows, limit, _offer_read, lambda o: str(o.id), scope)


async def _require_offer(
    session: AsyncSession, brand_id: uuid.UUID, offer_id: uuid.UUID, **kw: bool
) -> Offer:
    offer = await repo.get_offer(session, brand_id, offer_id, **kw)
    if offer is None:
        raise _not_found("offer", offer_id)
    return offer


async def get_offer(
    session: AsyncSession,
    brand_id: uuid.UUID,
    offer_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> OfferRead:
    await _require_brand(session, brand_id)
    return _offer_read(
        await _require_offer(session, brand_id, offer_id, include_archived=include_archived)
    )


async def update_offer(
    session: AsyncSession,
    actor: Actor,
    brand_id: uuid.UUID,
    offer_id: uuid.UUID,
    body: OfferUpdate,
) -> OfferRead:
    await _require_brand(session, brand_id, for_update=True)
    offer = await _require_offer(session, brand_id, offer_id)
    changes = _offer_values(body.model_dump(exclude_unset=True))
    if "audience_id" in changes:
        await _check_offer_audience(session, brand_id, changes["audience_id"])
    price = changes.get("price", offer.price)
    currency = changes.get("currency", offer.currency)
    if (price is None) != (currency is None):
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "price and currency must be set together",
            details={"field": "price", "reason": "PRICE_CURRENCY_PAIR"},
        )
    before = _snapshot(_offer_read(offer), list(changes))
    for field, value in changes.items():
        setattr(offer, field, value)
    offer = await repo.flush_row(session, offer, "offer")
    read = _offer_read(offer)
    if changes:
        await _audit(
            session,
            actor,
            "offer.update",
            "offers",
            offer.id,
            brand_id,
            before=before,
            after=_snapshot(read, list(changes)),
        )
    await session.commit()
    return read


async def archive_offer(
    session: AsyncSession, actor: Actor, brand_id: uuid.UUID, offer_id: uuid.UUID
) -> OfferRead:
    return await _set_offer_archived(session, actor, brand_id, offer_id, archive=True)


async def restore_offer(
    session: AsyncSession, actor: Actor, brand_id: uuid.UUID, offer_id: uuid.UUID
) -> OfferRead:
    return await _set_offer_archived(session, actor, brand_id, offer_id, archive=False)


async def _set_offer_archived(
    session: AsyncSession,
    actor: Actor,
    brand_id: uuid.UUID,
    offer_id: uuid.UUID,
    *,
    archive: bool,
) -> OfferRead:
    await _require_brand(session, brand_id, for_update=True)
    offer = await _require_offer(session, brand_id, offer_id, include_archived=True)
    if (offer.archived_at is not None) == archive:
        state = "already archived" if archive else "not archived"
        raise OmniGrowthError(ErrorCode.CONFLICT, f"offer is {state}")
    before = _snapshot(_offer_read(offer), ["archived_at"])
    offer.archived_at = datetime.now(UTC) if archive else None
    offer = await repo.flush_row(session, offer, "offer")
    read = _offer_read(offer)
    await _audit(
        session,
        actor,
        "offer.archive" if archive else "offer.restore",
        "offers",
        offer.id,
        brand_id,
        before=before,
        after=_snapshot(read, ["archived_at"]),
    )
    await session.commit()
    return read
