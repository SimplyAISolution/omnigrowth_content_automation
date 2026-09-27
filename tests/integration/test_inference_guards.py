"""Integration: inference guards added after the MVP milestone.

* Cross-process inference slots on the real Redis (A2 across workers): exclusive holding,
  bounded wait -> MODEL_UNAVAILABLE, crashed holder freed by TTL, release only by the owner.
* Bounded model-queue backlog: human requests are refused with RATE_LIMITED once the queue
  holds ``max_open_model_jobs`` open jobs; system follow-ups are never refused.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from omnigrowth.core.enums import ActorType, JobKind, JobStatus, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.state_machines import Actor
from omnigrowth.models import Job
from omnigrowth.providers.slots import RedisSlotLimiter
from omnigrowth.services import jobs as job_service

pytestmark = pytest.mark.integration

HUMAN = Actor(ActorType.HUMAN, "user-guard", UserRole.EDITOR)
SYSTEM = Actor(ActorType.SYSTEM, "system:test")


LEASE = 0.6


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    url = os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL not configured")
    client = Redis.from_url(url)
    try:
        yield client
    finally:
        await client.aclose()


def _limiter(redis: Redis) -> RedisSlotLimiter:
    return RedisSlotLimiter(redis, poll_interval_s=0.02)


async def _group(redis: Redis, limit: int = 1) -> str:
    """A fresh group whose slots were last released cleanly (no settle on first use)."""
    group = f"test-{uuid.uuid4().hex}"
    for index in range(limit):
        await redis.set(RedisSlotLimiter.names(group, index)[1], "1", ex=60)
    return group


async def test_slot_is_exclusive_and_waiters_are_bounded(redis: Redis) -> None:
    group = await _group(redis)
    order: list[str] = []
    async with _limiter(redis).slot(group, 1, lease_s=LEASE, max_wait_s=1):
        with pytest.raises(OmniGrowthError) as info:
            async with _limiter(redis).slot(group, 1, lease_s=LEASE, max_wait_s=0.1):
                order.append("second-inside")  # pragma: no cover - must not happen
        assert info.value.code is ErrorCode.MODEL_UNAVAILABLE
        assert info.value.details["reason"] == "SLOT_WAIT_TIMEOUT"
        order.append("first")

    async def hold() -> None:
        async with _limiter(redis).slot(group, 1, lease_s=LEASE, max_wait_s=1):
            order.append("a-in")
            await asyncio.sleep(0.1)
            order.append("a-out")

    async def wait_then_hold() -> None:
        await asyncio.sleep(0.02)
        async with _limiter(redis).slot(group, 1, lease_s=LEASE, max_wait_s=2):
            order.append("b-in")

    await asyncio.gather(hold(), wait_then_hold())
    assert order == ["first", "a-in", "a-out", "b-in"]


async def test_real_redis_lease_renewal_and_loss_never_overlap(redis: Redis) -> None:
    """Real Redis: a call 3x longer than the lease keeps it (renewal); deleting the key
    mid-call (data loss) cancels the holder before the next holder starts."""
    group = await _group(redis)
    slot, _ = RedisSlotLimiter.names(group, 0)
    spans: dict[str, list[float]] = {}

    async def call(name: str, seconds: float) -> None:
        spans[name] = [time.monotonic(), float("inf")]
        try:
            await asyncio.sleep(seconds)
        finally:
            spans[name][1] = time.monotonic()

    async with _limiter(redis).slot(group, 1, lease_s=LEASE, max_wait_s=1):
        await call("renewed", LEASE * 3)
        assert await redis.get(slot) is not None  # still ours after 3 leases

    errors: list[OmniGrowthError] = []

    async def holder() -> None:
        try:
            async with _limiter(redis).slot(group, 1, lease_s=LEASE, max_wait_s=2):
                await call("a", LEASE * 5)
        except OmniGrowthError as exc:
            errors.append(exc)

    async def intruder() -> None:
        await asyncio.sleep(LEASE * 0.5)
        await redis.delete(slot, RedisSlotLimiter.names(group, 0)[1])
        async with _limiter(redis).slot(group, 1, lease_s=LEASE, max_wait_s=5):
            await call("b", 0.05)

    await asyncio.gather(holder(), intruder())
    assert [e.details["reason"] for e in errors] == ["SLOT_LOST"]
    assert spans["a"][1] <= spans["b"][0]  # no overlap


async def test_crashed_holder_is_freed_by_its_lease_then_settled(redis: Redis) -> None:
    group = await _group(redis)
    slot, marker = RedisSlotLimiter.names(group, 0)
    await redis.delete(marker)
    # A crashed worker left its token behind; its lease runs out.
    await redis.set(slot, "dead-worker", px=200)
    started = time.monotonic()
    async with _limiter(redis).slot(group, 1, lease_s=LEASE, max_wait_s=2) as name:
        assert name == slot
        assert time.monotonic() - started >= 0.2 + LEASE * 0.9  # expiry + settle
        await redis.set(slot, "someone-else")  # someone else's lease must survive release
    assert await redis.get(slot) == b"someone-else"
    await redis.delete(slot)


async def test_limit_two_admits_two_holders(redis: Redis) -> None:
    group = await _group(redis, limit=2)
    async with (
        _limiter(redis).slot(group, 2, lease_s=LEASE, max_wait_s=1) as first,
        _limiter(redis).slot(group, 2, lease_s=LEASE, max_wait_s=1) as second,
    ):
        assert first != second
        with pytest.raises(OmniGrowthError):
            async with _limiter(redis).slot(group, 2, lease_s=LEASE, max_wait_s=0.05):
                pass  # pragma: no cover


async def _open_generate_jobs(session: AsyncSession) -> int:
    kinds = [k for k in JobKind if job_service.queue_for(k) == "generate"]
    count = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.kind.in_(kinds), Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
    )
    return int(count or 0)


async def test_model_queue_backlog_is_bounded_for_humans(session: AsyncSession) -> None:
    limit = await _open_generate_jobs(session) + 2
    for _ in range(2):
        await job_service.create_job(
            session,
            kind=JobKind.ASSET_GENERATION,
            actor=HUMAN,
            brand_id=None,
            max_open_jobs=limit,
        )
    with pytest.raises(OmniGrowthError) as info:
        await job_service.create_job(
            session,
            kind=JobKind.CONTENT_REGENERATION,
            actor=HUMAN,
            brand_id=None,
            max_open_jobs=limit,
        )
    assert info.value.code is ErrorCode.RATE_LIMITED
    assert info.value.http_status == 429
    assert info.value.details == {"limit": "max_open_model_jobs", "max": limit, "queue": "generate"}
    # System follow-ups are never refused (no half-finished work is stranded) ...
    await job_service.create_job(
        session, kind=JobKind.ASSET_GENERATION, actor=SYSTEM, brand_id=None, max_open_jobs=limit
    )
    # ... and non-model queues are not limited.
    await job_service.create_job(
        session, kind=JobKind.ANALYTICS_PULL, actor=HUMAN, brand_id=None, max_open_jobs=1
    )


async def test_concurrent_requests_cannot_overshoot_the_backlog_cap(engine: AsyncEngine) -> None:
    """Regression (reviewer m8): eight concurrent human requests race for three free places.
    Without the per-queue advisory lock each would count the same backlog and insert."""
    async with AsyncSession(engine) as probe:
        limit = await _open_generate_jobs(probe) + 3
    created: list[uuid.UUID] = []
    refused: list[OmniGrowthError] = []

    async def request() -> None:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                job_id = await job_service.create_job(
                    session,
                    kind=JobKind.ASSET_GENERATION,
                    actor=HUMAN,
                    brand_id=None,
                    max_open_jobs=limit,
                )
                await asyncio.sleep(0.05)  # widen the count-then-commit window
                await session.commit()
                created.append(job_id)
            except OmniGrowthError as exc:
                await session.rollback()
                refused.append(exc)

    try:
        await asyncio.gather(*(request() for _ in range(8)))
        assert len(created) == 3
        assert len(refused) == 5
        assert {e.code for e in refused} == {ErrorCode.RATE_LIMITED}
    finally:
        async with AsyncSession(engine) as cleanup:
            for job_id in created:
                await job_service.cancel_queued(cleanup, job_id, reason="test cleanup")
            await cleanup.commit()
