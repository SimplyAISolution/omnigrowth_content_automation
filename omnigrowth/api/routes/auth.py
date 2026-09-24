"""``/api/v1/auth`` — login, logout, me (ADR-0006). Login is the only unauthenticated route
besides health."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request, status

from omnigrowth.api.deps import RateLimiterDep, SessionDep, ViewerDep, client_ip
from omnigrowth.core.settings import get_settings
from omnigrowth.schemas.auth import LoginRequest, MeRead, TokenRead
from omnigrowth.security.ratelimit import parse_limits
from omnigrowth.services import auth as auth_service
from omnigrowth.storage import auth as auth_repo

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenRead)
async def login(
    body: LoginRequest, session: SessionDep, request: Request, limiter: RateLimiterDep
) -> TokenRead:
    # Per IP and per email, fail closed (API_PLAN section 6.4): brute force must be impossible
    # even when Redis is down.
    limits = parse_limits(get_settings().rate_limit_login)
    await limiter.hit("login-ip", client_ip(request), limits, fail_closed=True)
    email_key = hashlib.sha256(
        body.email.strip().lower().encode()
    ).hexdigest()  # no raw PII in Redis
    await limiter.hit("login-email", email_key, limits, fail_closed=True)
    result = await auth_service.login(
        session, email=body.email, password=body.password.get_secret_value()
    )
    return TokenRead(access_token=result.token, expires_at=result.expires_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(principal: ViewerDep, session: SessionDep) -> None:
    await auth_repo.revoke_token(session, principal.token_id)
    await session.commit()


@router.get("/me", response_model=MeRead)
async def me(principal: ViewerDep) -> MeRead:
    return MeRead(user_id=principal.user_id, email=principal.email, role=principal.role)
