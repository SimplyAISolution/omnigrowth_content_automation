"""Fixed-window rate limiting (API_PLAN §6.4; ADR-0006).

Backends: Redis (production; shared across API processes) and in-memory (tests / single
process). When the Redis backend is unreachable the limiter FAILS CLOSED for callers that
ask for it (login), and open otherwise.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import redis.asyncio as redis_asyncio
from redis.exceptions import RedisError

from omnigrowth.core.errors import ErrorCode, OmniGrowthError


@dataclass(frozen=True, slots=True)
class Limit:
    count: int
    window_s: int

    @classmethod
    def parse(cls, spec: str) -> Limit:
        """``"5/minute"``, ``"20/hour"``, ``"100/second"``."""
        amount, _, unit = spec.partition("/")
        windows = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
        if unit not in windows or not amount.isdigit() or int(amount) < 1:
            raise ValueError(f"invalid rate limit {spec!r}")
        return cls(int(amount), windows[unit])


def parse_limits(spec: str) -> tuple[Limit, ...]:
    return tuple(Limit.parse(part.strip()) for part in spec.split(";") if part.strip())


class RateLimitBackend(Protocol):
    async def incr(self, key: str, window_s: int) -> int: ...


class MemoryBackend:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    async def incr(self, key: str, window_s: int) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]


class RedisBackend:
    def __init__(self, url: str) -> None:
        self._client = redis_asyncio.from_url(url, socket_timeout=1.0, socket_connect_timeout=1.0)

    async def incr(self, key: str, window_s: int) -> int:
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_s + 1, nx=True)
            count, _ = await pipe.execute()
        return int(count)


class RateLimiter:
    def __init__(self, backend: RateLimitBackend, prefix: str = "og:rl") -> None:
        self._backend = backend
        self._prefix = prefix

    async def hit(
        self, scope: str, identity: str, limits: Sequence[Limit], *, fail_closed: bool
    ) -> None:
        """Count one request against every limit; raise RATE_LIMITED when any is exceeded."""
        now = time.time()
        for limit in limits:
            window = math.floor(now / limit.window_s)
            key = f"{self._prefix}:{scope}:{identity}:{limit.window_s}:{window}"
            try:
                count = await self._backend.incr(key, limit.window_s)
            except (RedisError, OSError) as exc:
                if fail_closed:
                    raise OmniGrowthError(
                        ErrorCode.RATE_LIMITED,
                        "rate limiter unavailable; try again shortly",
                        details={"reason": "LIMITER_UNAVAILABLE", "retry_after_s": 30},
                    ) from exc
                return
            if count > limit.count:
                retry_after = int((window + 1) * limit.window_s - now) + 1
                raise OmniGrowthError(
                    ErrorCode.RATE_LIMITED,
                    "too many requests",
                    details={"scope": scope, "retry_after_s": retry_after},
                )
