"""Integration: inference guards added after the MVP milestone.

* Cross-process inference slots on the real Redis (A2 across workers): exclusive holding,
  bounded wait -> MODEL_UNAVAILABLE, crashed holder freed by TTL, release only by the owner.
* Bounded model-queue backlog: human requests are refused with RATE_LIMITED once the queue
  holds ``max_open_model_jobs`` open jobs; system follow-ups are never refused.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import ActorType, JobKind, JobStatus, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.state_machines import Actor
from omnigrowth.models import Job
from omnigrowth.providers.slots import KEY_PREFIX, RedisSlotLimiter
from omnigrowth.services import jobs as job_service

pytestmark = pytest.mark.integration

HUMAN = Actor(ActorType.HUMAN, "user-guard", UserRole.EDITOR)
SYSTEM = Actor(ActorType.SYSTEM, "system:test")


@pytest.fixture
async def limiter() -> RedisSlotLimiter:
    url = os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL not configured")
    return RedisSlotLimiter(Redis.from_url(url), poll_interval_s=0.02)


def _group() -> str:
    return f"test-{uuid.uuid4().hex}"


async def test_slot_is_exclusive_and_waiters_are_bounded(limiter: RedisSlotLimiter) -> None:
    group = _group()
    order: list[str] = []
    try:
        async with limiter.slot(group, 1, ttl_s=30, max_wait_s=1):
            with pytest.raises(OmniGrowthError) as info:
                async with limiter.slot(group, 1, ttl_s=30, max_wait_s=0.1):
                    order.append("second-inside")  # pragma: no cover - must not happen
            assert info.value.code is ErrorCode.MODEL_UNAVAILABLE
            assert info.value.details["reason"] == "SLOT_WAIT_TIMEOUT"
            order.append("first")

        async def hold() -> None:
            async with limiter.slot(group, 1, ttl_s=30, max_wait_s=1):
                order.append("a-in")
                await asyncio.sleep(0.1)
                order.append("a-out")

        async def wait_then_hold() -> None:
            await asyncio.sleep(0.02)
            async with limiter.slot(group, 1, ttl_s=30, max_wait_s=2):
                order.append("b-in")

        await asyncio.gather(hold(), wait_then_hold())
        assert order == ["first", "a-in", "a-out", "b-in"]
    finally:
        await limiter.aclose()


async def test_crashed_holder_is_freed_by_ttl_and_only_the_owner_releases(
    limiter: RedisSlotLimiter,
) -> None:
    group = _group()
    key = f"{KEY_PREFIX}:{group}:0"
    raw = Redis.from_url(os.environ["REDIS_URL"])
    try:
        # A crashed worker left its token behind with a short TTL.
        await raw.set(key, "dead-worker", px=200)
        async with limiter.slot(group, 1, ttl_s=30, max_wait_s=2) as name:
            assert name == key
            assert await raw.get(key) != b"dead-worker"
            # Someone else's token must survive our release.
            await raw.set(key, "someone-else")
        assert await raw.get(key) == b"someone-else"
    finally:
        await raw.delete(key)
        await raw.aclose()
        await limiter.aclose()


async def test_limit_two_admits_two_holders(limiter: RedisSlotLimiter) -> None:
    group = _group()
    try:
        async with (
            limiter.slot(group, 2, ttl_s=30, max_wait_s=1) as first,
            limiter.slot(group, 2, ttl_s=30, max_wait_s=1) as second,
        ):
            assert first != second
            with pytest.raises(OmniGrowthError):
                async with limiter.slot(group, 2, ttl_s=30, max_wait_s=0.05):
                    pass  # pragma: no cover
    finally:
        await limiter.aclose()


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
