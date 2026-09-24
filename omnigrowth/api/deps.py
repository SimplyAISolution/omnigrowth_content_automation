"""Shared FastAPI dependencies: DB session, authenticated principal, role checks (ADR-0006)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import ActorType, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.logging import set_context
from omnigrowth.core.settings import get_settings
from omnigrowth.core.state_machines import Actor
from omnigrowth.security.ratelimit import RateLimiter, parse_limits
from omnigrowth.services.jobs import JobQueue, RqJobQueue
from omnigrowth.storage import auth as auth_repo
from omnigrowth.storage.db import get_sessionmaker
from omnigrowth.storage.uploads import UploadStore

_ROLE_RANK = {UserRole.VIEWER: 0, UserRole.EDITOR: 1, UserRole.OWNER: 2}


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request. Services commit explicitly; anything uncommitted rolls back."""
    async with get_sessionmaker()() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    token_id: uuid.UUID
    email: str
    role: UserRole

    @property
    def actor(self) -> Actor:
        return Actor(ActorType.HUMAN, str(self.user_id), self.role)


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise OmniGrowthError(ErrorCode.UNAUTHORIZED, "missing bearer token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise OmniGrowthError(ErrorCode.UNAUTHORIZED, "malformed Authorization header")
    return token.strip()


async def current_principal(
    session: SessionDep, authorization: Annotated[str | None, Header()] = None
) -> Principal:
    token = _bearer(authorization)
    principal = await auth_repo.resolve_token(session, token)
    if principal is None:
        raise OmniGrowthError(ErrorCode.UNAUTHORIZED, "invalid or expired token")
    await session.commit()  # persists last_used_at
    set_context(request_user=str(principal.user_id))
    return Principal(
        user_id=principal.user_id,
        token_id=principal.token_id,
        email=principal.email,
        role=principal.role,
    )


def require_role(minimum: UserRole) -> Callable[..., Awaitable[Principal]]:
    async def _check(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if _ROLE_RANK[principal.role] < _ROLE_RANK[minimum]:
            raise OmniGrowthError(
                ErrorCode.FORBIDDEN, f"requires role {minimum}", details={"role": principal.role}
            )
        return principal

    return _check


ViewerDep = Annotated[Principal, Depends(require_role(UserRole.VIEWER))]
EditorDep = Annotated[Principal, Depends(require_role(UserRole.EDITOR))]
OwnerDep = Annotated[Principal, Depends(require_role(UserRole.OWNER))]


# ------------------------------------------------------------------ infrastructure (overridable)
def get_job_queue() -> JobQueue:
    """RQ in production; tests override with RecordingJobQueue via dependency_overrides."""
    return RqJobQueue(get_settings().redis_url.get_secret_value())


def get_upload_store() -> UploadStore:
    return UploadStore(get_settings().upload_dir)


JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]
UploadStoreDep = Annotated[UploadStore, Depends(get_upload_store)]


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def ingest_rate_limit(
    principal: Annotated[Principal, Depends(require_role(UserRole.EDITOR))],
    limiter: RateLimiterDep,
) -> None:
    """Per-user limit on job-creating ingestion routes (API_PLAN section 6.4)."""
    await limiter.hit(
        "ingest",
        str(principal.user_id),
        parse_limits(get_settings().rate_limit_ingest),
        fail_closed=False,
    )


IngestRateLimit = Depends(ingest_rate_limit)
