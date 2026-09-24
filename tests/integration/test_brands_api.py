"""Integration: /api/v1/brands — brands, profile versions, audiences, offers (spec §5)."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from omnigrowth.core.enums import UserRole
from omnigrowth.models import AuditEvent, BrandProfile
from omnigrowth.storage.db import get_sessionmaker
from tests.integration.helpers import auth_headers

pytestmark = pytest.mark.integration

API = "/api/v1"


@pytest.fixture
async def client(fresh_database: str) -> AsyncIterator[httpx.AsyncClient]:
    """Real app; mounts the brands router only if the primary has not mounted it yet."""
    from omnigrowth.api.app import create_app
    from omnigrowth.api.routes import brands

    app = create_app()
    if not any(getattr(r, "path", "").startswith(f"{API}/brands") for r in app.routes):
        app.include_router(brands.router, prefix=API)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    from omnigrowth.storage import db

    if db.get_engine.cache_info().currsize:
        await db.get_engine().dispose()


@pytest.fixture
async def editor(client: httpx.AsyncClient) -> dict[str, str]:
    return await auth_headers(client, UserRole.EDITOR)


@pytest.fixture
async def owner(client: httpx.AsyncClient) -> dict[str, str]:
    return await auth_headers(client, UserRole.OWNER)


def _slug() -> str:
    return f"brand-{secrets.token_hex(4)}"


async def _create_brand(
    client: httpx.AsyncClient, headers: dict[str, str], **extra: Any
) -> dict[str, Any]:
    body = {"slug": _slug(), "name": "Acme AI", **extra}
    resp = await client.post(f"{API}/brands", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()
    return data


async def _audit(entity_id: str) -> list[AuditEvent]:
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.entity_id == uuid.UUID(entity_id))
                .order_by(AuditEvent.occurred_at, AuditEvent.id)
            )
        ).scalars()
        return list(rows)


# ---------------------------------------------------------------- brands
async def test_brand_crud_happy_path(
    client: httpx.AsyncClient, editor: dict[str, str], owner: dict[str, str]
) -> None:
    brand = await _create_brand(
        client,
        editor,
        timezone="America/New_York",
        profile={"niche": " local AI ", "voice": {"analytical": 0.9}, "content_pillars": ["AI"]},
    )
    assert brand["display_name"] == "Acme AI"  # defaults to name
    assert brand["auto_approve_enabled"] is False
    assert brand["archived_at"] is None
    bid = brand["id"]

    got = await client.get(f"{API}/brands/{bid}", headers=editor)
    assert got.status_code == 200 and got.json()["timezone"] == "America/New_York"

    profile = (await client.get(f"{API}/brands/{bid}/profile", headers=editor)).json()
    assert profile["version_no"] == 1 and profile["is_active"] is True
    assert profile["niche"] == "local AI" and profile["voice"] == {"analytical": 0.9}
    assert profile["actor_type"] == "HUMAN"

    patched = await client.patch(
        f"{API}/brands/{bid}",
        json={"display_name": "Acme", "default_routing_profile": "BALANCED"},
        headers=editor,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["display_name"] == "Acme"
    assert patched.json()["default_routing_profile"] == "BALANCED"

    bad_tz = await client.patch(
        f"{API}/brands/{bid}", json={"timezone": "Nope/Nope"}, headers=editor
    )
    assert bad_tz.status_code == 422 and bad_tz.json()["code"] == "VALIDATION_FAILURE"

    auto = await client.patch(
        f"{API}/brands/{bid}", json={"auto_approve_enabled": True}, headers=owner
    )
    assert auto.status_code == 200 and auto.json()["auto_approve_enabled"] is True

    archived = await client.post(f"{API}/brands/{bid}/archive", headers=owner)
    assert archived.status_code == 200 and archived.json()["archived_at"] is not None
    assert (await client.get(f"{API}/brands/{bid}", headers=editor)).status_code == 404
    assert (
        await client.get(f"{API}/brands/{bid}", params={"include_archived": True}, headers=editor)
    ).status_code == 200
    listed = (await client.get(f"{API}/brands", params={"limit": 200}, headers=editor)).json()
    assert bid not in {b["id"] for b in listed["items"]}
    assert (await client.post(f"{API}/brands/{bid}/archive", headers=owner)).status_code == 409

    restored = await client.post(f"{API}/brands/{bid}/restore", headers=owner)
    assert restored.status_code == 200 and restored.json()["archived_at"] is None

    actions = [e.action for e in await _audit(bid)]
    assert actions == [
        "brand.create",
        "brand.update",
        "brand.update",
        "brand.archive",
        "brand.restore",
    ]
    events = await _audit(bid)
    assert all(e.entity_type == "brands" and e.brand_id == uuid.UUID(bid) for e in events)
    update_event = events[1]
    assert update_event.before == {
        "display_name": "Acme AI",
        "default_routing_profile": "LOCAL_ONLY",
    }
    assert update_event.after == {"display_name": "Acme", "default_routing_profile": "BALANCED"}
    assert update_event.actor_type == "HUMAN"


async def test_create_without_profile_creates_empty_active_v1(
    client: httpx.AsyncClient, editor: dict[str, str]
) -> None:
    bid = (await _create_brand(client, editor))["id"]
    profile = (await client.get(f"{API}/brands/{bid}/profile", headers=editor)).json()
    assert profile["version_no"] == 1 and profile["products"] == [] and profile["voice"] == {}
    events = await _audit(profile["id"])
    assert [e.action for e in events] == ["brand_profile.version.create"]
    assert events[0].entity_type == "brand_profiles"


async def test_slug_conflict_is_409(client: httpx.AsyncClient, editor: dict[str, str]) -> None:
    slug = _slug()
    first = await client.post(f"{API}/brands", json={"slug": slug, "name": "A"}, headers=editor)
    assert first.status_code == 201
    dup = await client.post(f"{API}/brands", json={"slug": slug, "name": "B"}, headers=editor)
    assert dup.status_code == 409 and dup.json()["code"] == "CONFLICT"


async def test_roles(
    client: httpx.AsyncClient, editor: dict[str, str], owner: dict[str, str]
) -> None:
    viewer = await auth_headers(client, UserRole.VIEWER)
    body = {"slug": _slug(), "name": "X"}
    assert (await client.post(f"{API}/brands", json=body)).status_code == 401
    assert (await client.get(f"{API}/brands")).status_code == 401
    resp = await client.post(f"{API}/brands", json=body, headers=viewer)
    assert resp.status_code == 403 and resp.json()["code"] == "FORBIDDEN"

    bid = (await _create_brand(client, editor))["id"]
    assert (await client.get(f"{API}/brands/{bid}", headers=viewer)).status_code == 200
    assert (await client.post(f"{API}/brands/{bid}/archive", headers=editor)).status_code == 403
    assert (await client.post(f"{API}/brands/{bid}/restore", headers=editor)).status_code == 403
    auto = await client.patch(
        f"{API}/brands/{bid}", json={"auto_approve_enabled": False}, headers=editor
    )
    assert auto.status_code == 403 and auto.json()["code"] == "FORBIDDEN"
    assert (
        await client.patch(f"{API}/brands/{bid}", json={"display_name": "Y"}, headers=viewer)
    ).status_code == 403
    assert (
        await client.put(f"{API}/brands/{bid}/profile", json={}, headers=viewer)
    ).status_code == 403
    assert (
        await client.post(f"{API}/brands/{bid}/audiences", json={"name": "a"}, headers=viewer)
    ).status_code == 403


async def test_unknown_ids_are_404(client: httpx.AsyncClient, editor: dict[str, str]) -> None:
    missing = uuid.uuid4()
    for path in (
        f"/brands/{missing}",
        f"/brands/{missing}/profile",
        f"/brands/{missing}/audiences",
        f"/brands/{missing}/offers",
    ):
        resp = await client.get(f"{API}{path}", headers=editor)
        assert resp.status_code == 404 and resp.json()["code"] == "NOT_FOUND", path
    assert (await client.get(f"{API}/brands/not-a-uuid", headers=editor)).status_code == 422


async def test_pagination_cursor(client: httpx.AsyncClient, editor: dict[str, str]) -> None:
    created = {(await _create_brand(client, editor))["id"] for _ in range(3)}
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(100):
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get(f"{API}/brands", params=params, headers=editor)).json()
        assert len(page["items"]) <= 2
        seen.extend(b["id"] for b in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert created <= set(seen)
    assert len(seen) == len(set(seen))
    assert seen == sorted(seen, reverse=True)  # id DESC (UUIDv7: newest first)

    bad = await client.get(f"{API}/brands", params={"cursor": "garbage!"}, headers=editor)
    assert bad.status_code == 422 and bad.json()["code"] == "VALIDATION_FAILURE"
    first = (await client.get(f"{API}/brands", params={"limit": 1}, headers=editor)).json()
    other_filter = await client.get(
        f"{API}/brands",
        params={"cursor": first["next_cursor"], "include_archived": True},
        headers=editor,
    )
    assert other_filter.status_code == 422


# ---------------------------------------------------------------- profile versions
async def test_profile_versioning(client: httpx.AsyncClient, editor: dict[str, str]) -> None:
    bid = (
        await _create_brand(client, editor, profile={"mission": "v1 mission", "vocabulary": ["a"]})
    )["id"]
    v1 = (await client.get(f"{API}/brands/{bid}/profile", headers=editor)).json()

    put = await client.put(
        f"{API}/brands/{bid}/profile",
        json={"mission": "v2 mission", "expected_version_no": 1, "voice": {"humorous": "dry"}},
        headers=editor,
    )
    assert put.status_code == 201, put.text
    v2 = put.json()
    assert v2["version_no"] == 2 and v2["is_active"] is True and v2["id"] != v1["id"]
    assert put.headers["ETag"] == '"2"'

    active = (await client.get(f"{API}/brands/{bid}/profile", headers=editor)).json()
    assert active["id"] == v2["id"] and active["mission"] == "v2 mission"

    async with get_sessionmaker()() as session:
        rows = (
            (
                await session.execute(
                    select(BrandProfile)
                    .where(BrandProfile.brand_id == uuid.UUID(bid))
                    .order_by(BrandProfile.version_no)
                )
            )
            .scalars()
            .all()
        )
    assert [r.is_active for r in rows] == [False, True]  # exactly one active
    old = rows[0]
    assert old.mission == "v1 mission" and old.vocabulary == ["a"] and old.voice == {}

    stale = await client.put(
        f"{API}/brands/{bid}/profile", json={"expected_version_no": 1}, headers=editor
    )
    assert stale.status_code == 409 and stale.json()["code"] == "CONFLICT"
    stale_header = await client.put(
        f"{API}/brands/{bid}/profile", json={}, headers={**editor, "If-Match": '"1"'}
    )
    assert stale_header.status_code == 409
    ok_header = await client.put(
        f"{API}/brands/{bid}/profile", json={"niche": "n"}, headers={**editor, "If-Match": '"2"'}
    )
    assert ok_header.status_code == 201 and ok_header.json()["version_no"] == 3

    bad_voice = await client.put(
        f"{API}/brands/{bid}/profile", json={"voice": {"sarcastic": 1}}, headers=editor
    )
    assert bad_voice.status_code == 422 and bad_voice.json()["code"] == "VALIDATION_FAILURE"

    page1 = (
        await client.get(
            f"{API}/brands/{bid}/profile/versions", params={"limit": 2}, headers=editor
        )
    ).json()
    assert [p["version_no"] for p in page1["items"]] == [3, 2]
    page2 = (
        await client.get(
            f"{API}/brands/{bid}/profile/versions",
            params={"limit": 2, "cursor": page1["next_cursor"]},
            headers=editor,
        )
    ).json()
    assert [p["version_no"] for p in page2["items"]] == [1] and page2["next_cursor"] is None

    events = await _audit(v2["id"])
    assert len(events) == 1 and events[0].action == "brand_profile.version.create"
    assert events[0].metadata_["version_no"] == 2
    assert events[0].metadata_["previous_version_id"] == v1["id"]
    assert events[0].before is not None and events[0].before["mission"] == "v1 mission"


# ---------------------------------------------------------------- audiences
async def test_audience_crud_and_single_primary(
    client: httpx.AsyncClient, editor: dict[str, str]
) -> None:
    bid = (await _create_brand(client, editor))["id"]
    base = f"{API}/brands/{bid}/audiences"
    a1 = await client.post(
        base,
        json={
            "name": "CTOs",
            "is_primary": True,
            "pains": [" cost ", "COST", ""],
            "sophistication_level": 4,
        },
        headers=editor,
    )
    assert a1.status_code == 201, a1.text
    assert a1.json()["pains"] == ["cost"]
    assert a1.headers["Location"].endswith(a1.json()["id"])
    a1_id = a1.json()["id"]

    a2 = await client.post(base, json={"name": "Founders", "is_primary": True}, headers=editor)
    assert a2.status_code == 201
    a2_id = a2.json()["id"]
    assert (await client.get(f"{base}/{a1_id}", headers=editor)).json()["is_primary"] is False

    dup = await client.post(base, json={"name": "CTOs"}, headers=editor)
    assert dup.status_code == 409

    patched = await client.patch(f"{base}/{a1_id}", json={"is_primary": True}, headers=editor)
    assert patched.status_code == 200 and patched.json()["is_primary"] is True
    items = (await client.get(base, headers=editor)).json()["items"]
    assert [a["id"] for a in items if a["is_primary"]] == [a1_id]

    archived = await client.post(f"{base}/{a2_id}/archive", headers=editor)
    assert archived.status_code == 200
    assert a2_id not in {a["id"] for a in (await client.get(base, headers=editor)).json()["items"]}
    with_archived = (
        await client.get(base, params={"include_archived": True}, headers=editor)
    ).json()["items"]
    assert a2_id in {a["id"] for a in with_archived}
    assert (await client.get(f"{base}/{a2_id}", headers=editor)).status_code == 404
    assert (await client.post(f"{base}/{a2_id}/restore", headers=editor)).status_code == 200

    actions = [e.action for e in await _audit(a1_id)]
    assert actions[0] == "audience.create" and "audience.update" in actions
    assert all(e.entity_type == "audiences" for e in await _audit(a1_id))

    other_bid = (await _create_brand(client, editor))["id"]
    cross = await client.get(f"{API}/brands/{other_bid}/audiences/{a1_id}", headers=editor)
    assert cross.status_code == 404


# ---------------------------------------------------------------- offers
async def test_offer_crud_and_cross_brand_audience(
    client: httpx.AsyncClient, editor: dict[str, str]
) -> None:
    bid = (await _create_brand(client, editor))["id"]
    other_bid = (await _create_brand(client, editor))["id"]
    aud = (
        await client.post(f"{API}/brands/{bid}/audiences", json={"name": "A"}, headers=editor)
    ).json()
    foreign = (
        await client.post(f"{API}/brands/{other_bid}/audiences", json={"name": "B"}, headers=editor)
    ).json()
    base = f"{API}/brands/{bid}/offers"

    offer = await client.post(
        base,
        json={
            "name": "AI Audit",
            "audience_id": aud["id"],
            "price": "1999.00",
            "currency": "usd",
            "cta": "Book a call",
            "funnel_destination_url": "https://example.com/audit",
            "proof": ["case study"],
        },
        headers=editor,
    )
    assert offer.status_code == 201, offer.text
    o = offer.json()
    assert o["price"] == "1999.00" and o["currency"] == "USD"
    assert o["funnel_destination_url"] == "https://example.com/audit"
    oid = o["id"]

    cross = await client.post(
        base, json={"name": "Bad", "audience_id": foreign["id"]}, headers=editor
    )
    assert cross.status_code == 422 and cross.json()["code"] == "VALIDATION_FAILURE"
    cross_patch = await client.patch(
        f"{base}/{oid}", json={"audience_id": foreign["id"]}, headers=editor
    )
    assert cross_patch.status_code == 422

    bad_url = await client.post(
        base, json={"name": "Bad", "funnel_destination_url": "ftp://x.test/"}, headers=editor
    )
    assert bad_url.status_code == 422

    unpaired = await client.patch(f"{base}/{oid}", json={"currency": None}, headers=editor)
    assert unpaired.status_code == 422
    repriced = await client.patch(
        f"{base}/{oid}", json={"price": None, "currency": None, "audience_id": None}, headers=editor
    )
    assert repriced.status_code == 200, repriced.text
    assert repriced.json()["price"] is None and repriced.json()["audience_id"] is None

    assert (await client.get(f"{base}/{oid}", headers=editor)).status_code == 200
    assert (
        await client.get(f"{API}/brands/{other_bid}/offers/{oid}", headers=editor)
    ).status_code == 404

    assert (await client.post(f"{base}/{oid}/archive", headers=editor)).status_code == 200
    assert oid not in {x["id"] for x in (await client.get(base, headers=editor)).json()["items"]}
    assert (await client.post(f"{base}/{oid}/restore", headers=editor)).status_code == 200

    actions = [e.action for e in await _audit(oid)]
    assert actions == ["offer.create", "offer.update", "offer.archive", "offer.restore"]
