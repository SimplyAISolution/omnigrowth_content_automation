"""Cross-process inference slots (A2: one local GPU call at a time across ALL processes).

The router's asyncio semaphore only serialises calls inside one event loop. When several
workers run (``--scale worker=2``, a second ``poe worker``) they share the GPU but not the
semaphore. ``RedisSlotLimiter`` makes every model call hold one of ``limit`` Redis keys per
concurrency group, as a **lease**:

1. **Acquire** (atomic Lua): take a free key with our random token and a TTL of ``lease_s``.
   Waiting is bounded by ``max_wait_s`` -> MODEL_UNAVAILABLE ``SLOT_WAIT_TIMEOUT``.
2. **Renew** while the call runs: every ``lease_s / 3`` the TTL is extended, but only while
   the key still holds OUR token (compare-and-pexpire).
3. **Self-fencing.** The holder cancels its own call (MODEL_UNAVAILABLE ``SLOT_LOST``)
   - as soon as a renewal finds the key gone or owned by someone else, and
   - when it could not confirm ownership for ``lease_s - lease_s/3`` (Redis unreachable),
     which is BEFORE the key can expire, so nobody else can have acquired it yet.
4. **Settle after an unclean predecessor.** A clean release (the call returned) leaves a
   ``:clean`` marker. An acquirer that does not find the marker (the previous holder crashed,
   timed out, lost the lease, or Redis lost its data) waits one full ``lease_s`` before
   starting. Any previous holder that might still be running has, by then, either seen the
   loss at a renewal (<= lease_s/3) or self-cancelled (<= lease_s - lease_s/3).
5. **Release** deletes the key only while it holds our token (compare-and-delete); a
   cancellation during acquisition releases whatever it may have taken.

Guarantee: two holders of the same slot never run their calls at the same time, as long as
no process is frozen (suspended VM, laptop sleep) for longer than ``lease_s / 3`` while
holding a slot. What the model SERVER does after a client cancels is outside this module:
an aborted request is released as unclean, so the next call settles for ``lease_s`` to give
the server time to stop generating (LM Studio/llama.cpp stop on client disconnect —
UNVERIFIED per server version; see docs/audit/safeguards_post_mvp.md).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from omnigrowth.core.errors import ErrorCode, OmniGrowthError

log = logging.getLogger(__name__)

KEY_PREFIX = "omnigrowth:inference-slot"
POLL_INTERVAL_S = 0.25
DEFAULT_LEASE_S = 30.0

# Server-side Lua run with Redis EVAL (fixed constants below; no caller input is ever
# interpolated into a script, only passed as KEYS/ARGV). This is not Python eval.
# KEYS: slot, clean-marker. ARGV: token, lease ms. Returns 0 busy, 1 clean, 2 unclean.
ACQUIRE = """
if redis.call('exists', KEYS[1]) == 1 then return 0 end
redis.call('set', KEYS[1], ARGV[1], 'PX', ARGV[2])
local clean = redis.call('get', KEYS[2])
redis.call('del', KEYS[2])
if clean then return 1 end
return 2
"""
# KEYS: slot. ARGV: token, lease ms. Returns 1 renewed, 0 lost.
RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('pexpire', KEYS[1], ARGV[2])
  return 1
end
return 0
"""
# KEYS: slot, clean-marker. ARGV: token, clean flag ("1"/"0"). Returns 1 released, 0 not ours.
RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('del', KEYS[1])
  if ARGV[2] == '1' then redis.call('set', KEYS[2], '1') end
  return 1
end
return 0
"""

# Errors after which the model server is known to have finished with the request.
_COMPLETED_CODES = frozenset(
    {
        ErrorCode.LLM_OUTPUT_INVALID,
        ErrorCode.VALIDATION_FAILURE,
        ErrorCode.PROVIDER_RATE_LIMIT,
        ErrorCode.BUDGET_EXCEEDED,
    }
)


class SlotLimiter(Protocol):
    def slot(
        self, key: str, limit: int, *, lease_s: float, max_wait_s: float
    ) -> AbstractAsyncContextManager[str]: ...

    async def aclose(self) -> None: ...


def _slot_lost(group: str, reason: str) -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.MODEL_UNAVAILABLE,
        "inference slot lease lost; the call was cancelled to keep the GPU exclusive",
        details={"reason": "SLOT_LOST", "group": group, "cause": reason},
        retryable=True,
    )


class RedisSlotLimiter:
    def __init__(
        self,
        redis: Redis,
        *,
        poll_interval_s: float = POLL_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._redis = redis
        self._poll = poll_interval_s
        self._clock = clock
        self._sleep = sleep

    @classmethod
    def from_url(cls, url: str) -> RedisSlotLimiter:
        return cls(Redis.from_url(url))

    @staticmethod
    def names(key: str, index: int) -> tuple[str, str]:
        slot = f"{KEY_PREFIX}:{key}:{index}"
        return slot, f"{slot}:clean"

    async def _acquire(
        self, key: str, limit: int, token: str, lease_ms: int, max_wait_s: float
    ) -> tuple[int, bool, float]:
        """(slot index, predecessor released cleanly, time the winning ACQUIRE was SENT)."""
        deadline = self._clock() + max_wait_s
        while True:
            for index in range(limit):
                slot, clean = self.names(key, index)
                sent = self._clock()
                outcome = int(await self._redis.eval(ACQUIRE, 2, slot, clean, token, lease_ms))
                if outcome:
                    return index, outcome == 1, sent
            if self._clock() >= deadline:
                raise OmniGrowthError(
                    ErrorCode.MODEL_UNAVAILABLE,
                    "inference slot busy: another process is using the model",
                    details={"reason": "SLOT_WAIT_TIMEOUT", "group": key, "wait_s": max_wait_s},
                    retryable=True,
                )
            await self._sleep(self._poll)

    async def _release(self, key: str, index: int, token: str, *, clean: bool) -> None:
        slot, marker = self.names(key, index)
        try:
            await self._redis.eval(RELEASE, 2, slot, marker, token, "1" if clean else "0")
        except RedisError:
            # The lease TTL frees the slot; never mask the call's own outcome.
            log.warning("could not release inference slot", extra={"slot": slot})

    async def _release_any(self, key: str, limit: int, token: str) -> None:
        for index in range(limit):
            await self._release(key, index, token, clean=False)

    async def _guard(
        self,
        key: str,
        index: int,
        token: str,
        lease_s: float,
        holder: asyncio.Task[Any],
        lost: list[str],
        acquired_sent: float,
    ) -> None:
        """Renew the lease; cancel the holder the moment ownership is lost or unprovable.

        Every deadline is measured from when the confirming command was SENT (the server
        set the TTL no earlier), so self-cancellation always precedes key expiry."""
        slot, _ = self.names(key, index)
        interval = lease_s / 3
        lease_ms = max(int(lease_s * 1000), 1)
        confirmed_until = acquired_sent + lease_s - interval
        while True:
            await self._sleep(min(interval, max(confirmed_until - self._clock(), 0)))
            sent = self._clock()
            try:
                renewed = int(await self._redis.eval(RENEW, 1, slot, token, lease_ms))
            except RedisError:
                renewed = -1
            if renewed == 1:
                confirmed_until = sent + lease_s - interval
                continue
            if renewed == 0:
                lost.append("lease taken over or deleted")
                holder.cancel()
                return
            if self._clock() >= confirmed_until:
                lost.append("lease could not be confirmed before expiry")
                holder.cancel()
                return

    @asynccontextmanager
    async def slot(
        self, key: str, limit: int, *, lease_s: float = DEFAULT_LEASE_S, max_wait_s: float
    ) -> AsyncIterator[str]:
        token = secrets.token_hex(16)
        lease_ms = max(int(lease_s * 1000), 1)
        holder = asyncio.current_task()
        if holder is None:  # pragma: no cover - always called from a task
            raise RuntimeError("slot() must run inside an asyncio task")
        acquired = False
        try:
            index, predecessor_clean, sent = await self._acquire(
                key, limit, token, lease_ms, max_wait_s
            )
            acquired = True
        except RedisError as exc:
            raise OmniGrowthError(
                ErrorCode.QUEUE_FAILURE, "inference slot store unreachable", details={"group": key}
            ) from exc
        finally:
            if not acquired:
                # Failed or cancelled while an ACQUIRE may have landed: release anything
                # holding our token so no slot is orphaned (compare-and-delete).
                await asyncio.shield(self._release_any(key, limit, token))
        lost: list[str] = []
        guard = asyncio.create_task(self._guard(key, index, token, lease_s, holder, lost, sent))
        clean = False
        try:
            if not predecessor_clean:
                # A previous holder may still be finishing (crash, timeout, lost lease):
                # wait one full lease so it has seen the loss or self-cancelled.
                await self._sleep(lease_s)
            yield f"{KEY_PREFIX}:{key}:{index}"
            clean = True
        except asyncio.CancelledError:
            if lost and holder.uncancel() == 0:
                raise _slot_lost(key, lost[0]) from None
            raise
        except OmniGrowthError as exc:
            clean = exc.code in _COMPLETED_CODES and not lost
            raise
        finally:
            guard.cancel()
            await asyncio.gather(guard, return_exceptions=True)
            await asyncio.shield(self._release(key, index, token, clean=clean and not lost))

    async def aclose(self) -> None:
        await self._redis.aclose()
