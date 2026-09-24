"""Authentication use-cases (ADR-0006): login, logout, first-owner bootstrap."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.security.auth import hash_password, verify_password
from omnigrowth.storage import auth as repo

log = logging.getLogger(__name__)

SESSION_TTL = timedelta(hours=12)  # API_PLAN: session tokens 12 h


@dataclass(frozen=True, slots=True)
class LoginResult:
    token: str
    expires_at: datetime
    user: repo.UserRecord


async def login(session: AsyncSession, *, email: str, password: str) -> LoginResult:
    user = await repo.get_user_by_email(session, email)
    # verify_password always performs one argon2 verification (no user enumeration by timing).
    ok = await asyncio.to_thread(verify_password, user.password_hash if user else None, password)
    if user is None or not ok or not user.is_active:
        log.warning("login failed", extra={"reason": "invalid_credentials"})
        raise OmniGrowthError(ErrorCode.UNAUTHORIZED, "invalid email or password")
    issued, expires_at = await repo.create_token(
        session, user_id=user.id, name="session", ttl=SESSION_TTL
    )
    await session.commit()
    log.info(
        "login succeeded", extra={"user_id": str(user.id), "token_prefix": issued.token_prefix}
    )
    return LoginResult(token=issued.token, expires_at=expires_at, user=user)


async def create_owner(
    session: AsyncSession, *, email: str, display_name: str, password: str
) -> repo.UserRecord:
    """Bootstrap the first OWNER. Refuses if an OWNER already exists (no self-signup)."""
    if await repo.count_owners(session) > 0:
        raise OmniGrowthError(ErrorCode.CONFLICT, "an OWNER already exists")
    user = await repo.create_user(
        session,
        email=email,
        display_name=display_name,
        role=UserRole.OWNER,
        password_hash=hash_password(password),
    )
    await session.commit()
    return user
