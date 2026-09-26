"""Integration helpers: create users directly (bypassing first-owner bootstrap) and log in."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from typing import Any

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


SKIP_IK = "X-Test-No-Idempotency-Key"


async def add_idempotency_key(request: httpx.Request) -> None:
    """httpx request hook: every POST gets a fresh Idempotency-Key (API_PLAN §1.6), like a
    well-behaved client. Send ``X-Test-No-Idempotency-Key`` to test the missing-key path;
    set the header yourself to test replays."""
    if request.headers.pop(SKIP_IK, None) is not None:
        return
    if request.method == "POST" and "idempotency-key" not in request.headers:
        request.headers["Idempotency-Key"] = uuid.uuid4().hex


IK_HOOKS: dict[str, list[Callable[..., Any]]] = {"request": [add_idempotency_key]}
