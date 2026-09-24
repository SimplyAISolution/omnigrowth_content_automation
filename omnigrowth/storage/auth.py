"""Persistence for users and API tokens (ADR-0006)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.models import ApiToken, User
from omnigrowth.security.auth import IssuedToken, hash_token, issue_token


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: uuid.UUID
    email: str
    display_name: str
    role: UserRole
    is_active: bool
    password_hash: str


@dataclass(frozen=True, slots=True)
class TokenPrincipal:
    token_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: UserRole
    expires_at: datetime | None


def _record(user: User) -> UserRecord:
    return UserRecord(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=UserRole(user.role),
        is_active=user.is_active,
        password_hash=user.password_hash,
    )


async def create_user(
    session: AsyncSession, *, email: str, display_name: str, role: UserRole, password_hash: str
) -> UserRecord:
    user = User(
        id=uuid7(),
        email=email.strip(),
        display_name=display_name.strip(),
        role=role,
        password_hash=password_hash,
        is_active=True,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise OmniGrowthError(ErrorCode.CONFLICT, "a user with this email already exists") from exc
    return _record(user)


async def get_user_by_email(session: AsyncSession, email: str) -> UserRecord | None:
    user = (
        await session.execute(
            select(User).where(User.email == email.strip(), User.archived_at.is_(None))
        )
    ).scalar_one_or_none()
    return _record(user) if user is not None else None


async def count_owners(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.count()).select_from(User).where(User.role == UserRole.OWNER)
            )
        ).scalar_one()
    )


async def create_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    ttl: timedelta,
    scopes: list[str] | None = None,
) -> tuple[IssuedToken, datetime]:
    issued = issue_token()
    expires_at = datetime.now(UTC) + ttl
    session.add(
        ApiToken(
            id=uuid7(),
            user_id=user_id,
            name=name,
            token_prefix=issued.token_prefix,
            token_hash=issued.token_hash,
            scopes=scopes or [],
            expires_at=expires_at,
        )
    )
    await session.flush()
    return issued, expires_at


async def resolve_token(session: AsyncSession, token: str) -> TokenPrincipal | None:
    """Return the principal for a live token (not revoked, not expired, user active)."""
    now = datetime.now(UTC)
    row = (
        await session.execute(
            select(ApiToken, User)
            .join(User, User.id == ApiToken.user_id)
            .where(
                ApiToken.token_hash == hash_token(token),
                ApiToken.revoked_at.is_(None),
                (ApiToken.expires_at.is_(None)) | (ApiToken.expires_at > now),
                User.is_active.is_(True),
                User.archived_at.is_(None),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    api_token, user = row
    await session.execute(
        update(ApiToken).where(ApiToken.id == api_token.id).values(last_used_at=now)
    )
    return TokenPrincipal(
        token_id=api_token.id,
        user_id=user.id,
        email=user.email,
        role=UserRole(user.role),
        expires_at=api_token.expires_at,
    )


async def revoke_token(session: AsyncSession, token_id: uuid.UUID) -> None:
    await session.execute(
        update(ApiToken)
        .where(ApiToken.id == token_id, ApiToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
