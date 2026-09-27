"""Cross-process inference lease (providers/slots.py): regression tests for the Phase 8
reviewer findings — the lease outliving a slow call, ownership loss, Redis outages and
cancellation during acquisition. The key property: a second call never starts while the
previous one is still executing, including when the first holder's ownership is lost.

``MemoryRedis`` interprets the three Lua scripts with the same semantics as Redis (TTL in
wall-clock monotonic time) and can simulate data loss and outages.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.providers.slots import ACQUIRE, RELEASE, RENEW, RedisSlotLimiter

LEASE = 0.6


class MemoryRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}
        self.down = False
        self.block_acquire: asyncio.Event | None = None

    def _get(self, key: str) -> str | None:
        item = self.store.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and time.monotonic() >= expires:
            del self.store[key]
            return None
        return value

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        if self.down:
            raise RedisConnectionError("simulated outage")
        keys, argv = [str(a) for a in args[:numkeys]], [str(a) for a in args[numkeys:]]
        if script == ACQUIRE:
            if self.block_acquire is not None:
                await self.block_acquire.wait()
            if self._get(keys[0]) is not None:
                return 0
            self.store[keys[0]] = (argv[0], time.monotonic() + int(argv[1]) / 1000)
            clean = self._get(keys[1])
            self.store.pop(keys[1], None)
            return 1 if clean else 2
        if script == RENEW:
            if self._get(keys[0]) == argv[0]:
                self.store[keys[0]] = (argv[0], time.monotonic() + int(argv[1]) / 1000)
                return 1
            return 0
        if script == RELEASE:
            if self._get(keys[0]) == argv[0]:
                del self.store[keys[0]]
                if argv[1] == "1":
                    self.store[keys[1]] = ("1", None)
                return 1
            return 0
        raise AssertionError("unknown script")

    async def aclose(self) -> None:
        return None


def limiter(redis: MemoryRedis) -> RedisSlotLimiter:
    return RedisSlotLimiter(redis, poll_interval_s=0.01)  # type: ignore[arg-type]


class Timeline:
    """Records [start, end] of every simulated GPU call to prove exclusivity."""

    def __init__(self) -> None:
        self.spans: dict[str, list[float]] = {}

    async def call(self, name: str, seconds: float) -> None:
        self.spans[name] = [time.monotonic(), float("inf")]
        try:
            await asyncio.sleep(seconds)
        finally:
            self.spans[name][1] = time.monotonic()

    def overlaps(self) -> list[tuple[str, str]]:
        names = sorted(self.spans)
        return [
            (a, b)
            for i, a in enumerate(names)
            for b in names[i + 1 :]
            if self.spans[a][0] < self.spans[b][1] and self.spans[b][0] < self.spans[a][1]
        ]


async def _clean_start(redis: MemoryRedis, group: str) -> None:
    """Mark the slot as cleanly released so a first acquirer does not settle."""
    _, marker = RedisSlotLimiter.names(group, 0)
    redis.store[marker] = ("1", None)


async def test_lease_is_renewed_while_a_call_outlives_it() -> None:
    redis = MemoryRedis()
    await _clean_start(redis, "g")
    timeline = Timeline()

    async def first() -> None:
        async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=5):
            await timeline.call("a", LEASE * 3)  # 3x the lease: only renewal keeps it

    async def second() -> None:
        await asyncio.sleep(LEASE * 1.5)  # past the original TTL
        with pytest.raises(OmniGrowthError) as info:
            async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=LEASE * 0.5):
                await timeline.call("b", 0)  # pragma: no cover - must not start
        assert info.value.details["reason"] == "SLOT_WAIT_TIMEOUT"

    await asyncio.gather(first(), second())
    assert "b" not in timeline.spans


async def test_lost_ownership_cancels_the_call_before_anyone_else_starts() -> None:
    """Redis loses the key mid-call (restart without persistence, manual delete): the new
    acquirer finds no clean marker and settles a full lease; the old holder sees the loss at
    its next renewal and is cancelled first. The calls never overlap."""
    redis = MemoryRedis()
    await _clean_start(redis, "g")
    timeline = Timeline()
    outcome: dict[str, BaseException | None] = {}

    async def holder() -> None:
        try:
            async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=5):
                await timeline.call("a", LEASE * 5)
            outcome["a"] = None
        except OmniGrowthError as exc:
            outcome["a"] = exc

    async def intruder() -> None:
        await asyncio.sleep(LEASE * 0.5)
        redis.store.clear()  # data loss: key AND clean marker gone
        async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=5):
            await timeline.call("b", 0.05)

    await asyncio.gather(holder(), intruder())
    err = outcome["a"]
    assert isinstance(err, OmniGrowthError)
    assert err.code is ErrorCode.MODEL_UNAVAILABLE and err.details["reason"] == "SLOT_LOST"
    assert timeline.spans["a"][1] - timeline.spans["a"][0] < LEASE * 2  # cut short
    assert timeline.overlaps() == []
    assert timeline.spans["a"][1] <= timeline.spans["b"][0]


async def test_takeover_by_another_token_cancels_the_holder() -> None:
    redis = MemoryRedis()
    await _clean_start(redis, "g")
    slot, _ = RedisSlotLimiter.names("g", 0)
    started = time.monotonic()
    with pytest.raises(OmniGrowthError) as info:
        async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=5):
            redis.store[slot] = ("someone-else", time.monotonic() + 60)
            await asyncio.sleep(LEASE * 5)
    assert info.value.details["reason"] == "SLOT_LOST"
    assert time.monotonic() - started < LEASE  # detected at the first renewal (lease/3)
    assert redis.store[slot][0] == "someone-else"  # never released someone else's lease


async def test_redis_outage_self_cancels_before_the_lease_could_expire() -> None:
    redis = MemoryRedis()
    await _clean_start(redis, "g")
    slot, _ = RedisSlotLimiter.names("g", 0)
    with pytest.raises(OmniGrowthError) as info:
        async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=5):
            acquired_until = redis.store[slot][1]
            redis.down = True
            await asyncio.sleep(LEASE * 5)
    assert info.value.details["reason"] == "SLOT_LOST"
    assert acquired_until is not None
    # Cancelled while the key still existed, so nobody else could have started.
    assert time.monotonic() < acquired_until + LEASE / 3


async def test_unclean_predecessor_forces_a_settle_period() -> None:
    redis = MemoryRedis()  # no clean marker: e.g. the previous holder crashed
    started = time.monotonic()
    async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=5):
        entered = time.monotonic()
    assert entered - started >= LEASE * 0.95
    # A clean release lets the next holder start at once.
    started = time.monotonic()
    async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=5):
        entered = time.monotonic()
    assert entered - started < LEASE * 0.5


async def test_failed_call_is_released_as_unclean() -> None:
    redis = MemoryRedis()
    await _clean_start(redis, "g")
    with pytest.raises(OmniGrowthError):
        async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=5):
            raise OmniGrowthError(ErrorCode.MODEL_TIMEOUT, "server may still be generating")
    _, marker = RedisSlotLimiter.names("g", 0)
    assert marker not in redis.store  # next holder must settle


async def test_cancellation_during_acquisition_leaves_no_orphan() -> None:
    redis = MemoryRedis()
    redis.block_acquire = asyncio.Event()

    async def acquire() -> None:
        async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=5):
            pass  # pragma: no cover

    task = asyncio.create_task(acquire())
    await asyncio.sleep(0.05)
    task.cancel()
    redis.block_acquire.set()  # the SET lands after the caller was cancelled
    with pytest.raises(asyncio.CancelledError):
        await task
    slot, _ = RedisSlotLimiter.names("g", 0)
    assert redis._get(slot) is None


async def test_many_workers_never_overlap() -> None:
    redis = MemoryRedis()
    await _clean_start(redis, "g")
    timeline = Timeline()

    async def worker(n: int) -> None:
        async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=10):
            await timeline.call(f"w{n}", 0.05)

    await asyncio.gather(*(worker(n) for n in range(6)))
    assert len(timeline.spans) == 6 and timeline.overlaps() == []


async def test_external_cancellation_is_not_reported_as_slot_loss() -> None:
    redis = MemoryRedis()
    await _clean_start(redis, "g")

    async def call() -> None:
        async with limiter(redis).slot("g", 1, lease_s=LEASE, max_wait_s=5):
            await asyncio.sleep(10)

    task = asyncio.create_task(call())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    slot, _ = RedisSlotLimiter.names("g", 0)
    assert redis._get(slot) is None  # released
