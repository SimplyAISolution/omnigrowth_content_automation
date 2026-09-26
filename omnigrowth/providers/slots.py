"""Cross-process inference slots (A2: one local GPU call at a time across ALL processes).

The router's asyncio semaphore only serialises calls inside one event loop. When several
workers run (``--scale worker=2``, a second ``poe worker``), they share the GPU but not the
semaphore. ``RedisSlotLimiter`` holds one of ``limit`` Redis keys per concurrency group:

* acquire = ``SET key token NX PX ttl`` on any free slot; wait (poll) up to ``max_wait_s``,
  then fail with MODEL_UNAVAILABLE (reason ``SLOT_WAIT_TIMEOUT``) — a bounded queue, never
  an unbounded pile-up;
* the TTL is the provider timeout plus a margin, so a crashed holder frees its slot;
* release deletes the key only while it still holds our token (compare-and-delete).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from omnigrowth.core.errors import ErrorCode, OmniGrowthError

log = logging.getLogger(__name__)

KEY_PREFIX = "omnigrowth:inference-slot"
POLL_INTERVAL_S = 0.25
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end
return 0
"""


class SlotLimiter(Protocol):
    def slot(
        self, key: str, limit: int, *, ttl_s: float, max_wait_s: float
    ) -> AbstractAsyncContextManager[str]: ...

    async def aclose(self) -> None: ...


class RedisSlotLimiter:
    def __init__(self, redis: Redis, *, poll_interval_s: float = POLL_INTERVAL_S) -> None:
        self._redis = redis
        self._poll = poll_interval_s

    @classmethod
    def from_url(cls, url: str) -> RedisSlotLimiter:
        return cls(Redis.from_url(url))

    async def _try(self, key: str, limit: int, token: str, ttl_ms: int) -> str | None:
        for index in range(limit):
            name = f"{KEY_PREFIX}:{key}:{index}"
            if await self._redis.set(name, token, nx=True, px=ttl_ms):
                return name
        return None

    @asynccontextmanager
    async def slot(
        self, key: str, limit: int, *, ttl_s: float, max_wait_s: float
    ) -> AsyncIterator[str]:
        token = secrets.token_hex(16)
        ttl_ms = max(int(ttl_s * 1000), 1)
        deadline = time.monotonic() + max_wait_s
        try:
            while (name := await self._try(key, limit, token, ttl_ms)) is None:
                if time.monotonic() >= deadline:
                    raise OmniGrowthError(
                        ErrorCode.MODEL_UNAVAILABLE,
                        "inference slot busy: another process is using the model",
                        details={"reason": "SLOT_WAIT_TIMEOUT", "group": key, "wait_s": max_wait_s},
                        retryable=True,
                    )
                await asyncio.sleep(self._poll)
        except RedisError as exc:
            raise OmniGrowthError(
                ErrorCode.QUEUE_FAILURE,
                "inference slot store unreachable",
                details={"group": key},
            ) from exc
        try:
            yield name
        finally:
            try:
                await self._redis.eval(_RELEASE, 1, name, token)
            except RedisError:
                # The TTL frees the slot; never mask the call's own outcome.
                log.warning("could not release inference slot", extra={"slot": name})

    async def aclose(self) -> None:
        await self._redis.aclose()
