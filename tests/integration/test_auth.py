"""Integration: login, bearer auth, roles, logout, first-owner bootstrap (ADR-0006)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from omnigrowth.core.enums import UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.models import ApiToken
from omnigrowth.services.auth import create_owner
from omnigrowth.storage.db import get_sessionmaker
from tests.integration.helpers import PASSWORD, auth_headers, login, make_user

pytestmark = pytest.mark.integration


async def test_login_returns_opaque_token_stored_only_as_hash(
    api_client: httpx.AsyncClient,
) -> None:
    email = await make_user(UserRole.EDITOR)
    headers = await login(api_client, email)
    token = headers["Authorization"].split()[1]
    assert token.startswith("og_")
    async with get_sessionmaker()() as session:
        stored = (await session.execute(select(ApiToken.token_hash))).scalars().all()
    assert token not in stored  # never stored in clear
    me = await api_client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["role"] == "EDITOR"


@pytest.mark.parametrize(("email_ok", "password"), [(True, "wrong password!!"), (False, PASSWORD)])
async def test_bad_credentials_are_401_without_enumeration(
    api_client: httpx.AsyncClient, email_ok: bool, password: str
) -> None:
    email = await make_user(UserRole.VIEWER) if email_ok else "nobody@example.test"
    resp = await api_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"
    assert resp.json()["message"] == "invalid email or password"  # same message either way


@pytest.mark.parametrize(
    "header",
    [None, "Basic abc", "Bearer ", "Bearer og_not-a-real-token"],
)
async def test_protected_route_rejects_missing_or_bad_tokens(
    api_client: httpx.AsyncClient, header: str | None
) -> None:
    headers = {"Authorization": header} if header is not None else {}
    resp = await api_client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_expired_and_logged_out_tokens_are_rejected(api_client: httpx.AsyncClient) -> None:
    headers = await auth_headers(api_client, UserRole.EDITOR)
    assert (await api_client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
    assert (await api_client.get("/api/v1/auth/me", headers=headers)).status_code == 401

    headers = await auth_headers(api_client, UserRole.EDITOR)
    async with get_sessionmaker()() as session:
        await session.execute(
            update(ApiToken)
            .where(ApiToken.revoked_at.is_(None))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    assert (await api_client.get("/api/v1/auth/me", headers=headers)).status_code == 401


async def test_create_owner_bootstraps_once(isolated_database: str) -> None:
    async with get_sessionmaker()() as session:
        user = await create_owner(
            session, email="owner@example.test", display_name="Owner", password=PASSWORD
        )
        assert user.role is UserRole.OWNER
    async with get_sessionmaker()() as session:
        with pytest.raises(OmniGrowthError) as exc:
            await create_owner(
                session, email="second@example.test", display_name="X", password=PASSWORD
            )
    assert exc.value.code is ErrorCode.CONFLICT
    from omnigrowth.storage import db

    await db.get_engine().dispose()


async def test_short_password_rejected(isolated_database: str) -> None:
    async with get_sessionmaker()() as session:
        with pytest.raises(OmniGrowthError) as exc:
            await create_owner(session, email="o@example.test", display_name="O", password="short")
    assert exc.value.code is ErrorCode.VALIDATION_FAILURE
    from omnigrowth.storage import db

    await db.get_engine().dispose()
