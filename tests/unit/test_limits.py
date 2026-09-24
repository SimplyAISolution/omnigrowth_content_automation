"""Rate limiter (API_PLAN §6.4) and request-body size guard (§6.2)."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request

from omnigrowth.api.body_limit import JSON_LIMIT, BodySizeLimitMiddleware
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.security.ratelimit import Limit, MemoryBackend, RateLimiter, parse_limits


# ------------------------------------------------------------------ rate limiter
def test_parse_limits() -> None:
    assert parse_limits("5/minute;20/hour") == (Limit(5, 60), Limit(20, 3600))
    with pytest.raises(ValueError):
        parse_limits("five/minute")


async def test_limit_exceeded_raises_rate_limited_with_retry_after() -> None:
    limiter = RateLimiter(MemoryBackend())
    for _ in range(3):
        await limiter.hit("login-ip", "1.2.3.4", [Limit(3, 60)], fail_closed=True)
    with pytest.raises(OmniGrowthError) as exc:
        await limiter.hit("login-ip", "1.2.3.4", [Limit(3, 60)], fail_closed=True)
    assert exc.value.code is ErrorCode.RATE_LIMITED
    assert 1 <= exc.value.details["retry_after_s"] <= 61
    # Separate identities are independent.
    await limiter.hit("login-ip", "5.6.7.8", [Limit(3, 60)], fail_closed=True)


class _DownBackend:
    async def incr(self, key: str, window_s: int) -> int:
        raise ConnectionRefusedError("redis down")


async def test_backend_down_fails_closed_only_when_asked() -> None:
    limiter = RateLimiter(_DownBackend())
    with pytest.raises(OmniGrowthError) as exc:
        await limiter.hit("login-ip", "x", [Limit(5, 60)], fail_closed=True)
    assert exc.value.details["reason"] == "LIMITER_UNAVAILABLE"
    await limiter.hit("ingest", "x", [Limit(5, 60)], fail_closed=False)  # no raise


# ------------------------------------------------------------------ body size guard
def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    return app


async def test_declared_oversize_body_is_rejected_before_the_app() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://t"
    ) as c:
        resp = await c.post("/echo", content=b"x" * (JSON_LIMIT + 1))
    assert resp.status_code == 413
    assert resp.json()["code"] == "VALIDATION_FAILURE"


async def test_streamed_oversize_body_without_length_is_rejected() -> None:
    async def chunks():  # type: ignore[no-untyped-def]
        for _ in range(3):
            yield b"x" * (JSON_LIMIT // 2 + 1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://t"
    ) as c:
        resp = await c.post("/echo", content=chunks())
    assert resp.status_code == 413


async def test_body_under_limit_passes() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://t"
    ) as c:
        resp = await c.post("/echo", content=b"x" * 100)
    assert resp.status_code == 200 and resp.json() == {"size": 100}
