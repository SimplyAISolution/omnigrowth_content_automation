"""Integration helpers: create users directly (bypassing first-owner bootstrap) and log in."""

from __future__ import annotations

import secrets

import httpx

from omnigrowth.core.enums import UserRole
from omnigrowth.security.auth import hash_password
from omnigrowth.storage import auth as auth_repo
from omnigrowth.storage.db import get_sessionmaker

PASSWORD = "correct horse battery staple"  # test-only credential for throwaway databases


async def make_user(role: UserRole) -> str:
    email = f"{role.value.lower()}-{secrets.token_hex(4)}@example.test"
    async with get_sessionmaker()() as session:
        await auth_repo.create_user(
            session,
            email=email,
            display_name=role.value.title(),
            role=role,
            password_hash=hash_password(PASSWORD),
        )
        await session.commit()
    return email


async def login(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def auth_headers(client: httpx.AsyncClient, role: UserRole) -> dict[str, str]:
    return await login(client, await make_user(role))
