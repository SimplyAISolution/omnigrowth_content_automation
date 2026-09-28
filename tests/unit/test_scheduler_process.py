"""Scheduler process loop (Phase 2 FOUNDATION): DB-down tick, timeout, heartbeat, stop."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import omnigrowth.publishing.scheduler as sched_outbox
import omnigrowth.scheduling.__main__ as sched
from omnigrowth.core.errors import ErrorCode, OmniGrowthError


@pytest.fixture(autouse=True)
def _heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "hb"
    monkeypatch.setattr(sched, "HEARTBEAT_FILE", path)

    async def no_reap() -> int:
        return 0

    monkeypatch.setattr(sched, "reap_stuck_jobs", no_reap)

    async def no_outbox(**_: object) -> sched_outbox.TickResult:
        return sched_outbox.TickResult()

    monkeypatch.setattr(sched, "run_once", no_outbox)  # unit tests never reach a database

    async def no_purge() -> int:
        return 0

    monkeypatch.setattr(sched, "purge_idempotency_keys", no_purge)

    async def no_pulls(now: object) -> int:
        return 0

    monkeypatch.setattr(sched, "enqueue_analytics_pulls", no_pulls)

    async def no_experiments(now: object) -> dict[str, int]:
        return {}

    monkeypatch.setattr(sched, "sync_experiment_states", no_experiments)
    return path


async def test_tick_ok_touches_heartbeat(monkeypatch: pytest.MonkeyPatch, _heartbeat: Path) -> None:
    async def ok() -> None:
        return None

    monkeypatch.setattr(sched, "ping_database", ok)
    assert await sched.tick() is True
    assert _heartbeat.exists()


async def test_tick_db_down_does_not_touch_heartbeat(
    monkeypatch: pytest.MonkeyPatch, _heartbeat: Path
) -> None:
    async def down() -> None:
        raise OmniGrowthError(ErrorCode.DATABASE_FAILURE, "database unreachable")

    monkeypatch.setattr(sched, "ping_database", down)
    assert await sched.tick() is False
    assert not _heartbeat.exists()


async def test_tick_times_out_on_hung_database(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang() -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(sched, "ping_database", hang)
    monkeypatch.setattr(sched, "DB_PING_TIMEOUT_S", 0.05)
    assert await sched.tick() is False


async def test_run_honours_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def ok() -> None:
        nonlocal calls
        calls += 1

    class _Engine:
        async def dispose(self) -> None:
            return None

    async def idle_webhooks(stop: asyncio.Event) -> None:
        await stop.wait()

    monkeypatch.setattr(sched, "ping_database", ok)
    monkeypatch.setattr(sched, "get_engine", lambda: _Engine())
    monkeypatch.setattr(sched, "webhook_loop", idle_webhooks)
    stop = asyncio.Event()
    task = asyncio.create_task(sched.run(stop, tick_seconds=0.01))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert calls >= 1


def test_model_bound_jobs_get_longer_time_budgets_than_the_reaper_default() -> None:
    from datetime import timedelta

    from omnigrowth.core.enums import JobKind
    from omnigrowth.services.jobs import job_timeout_s, queue_for
    from omnigrowth.workflows.tasks import stuck_after

    assert job_timeout_s(queue_for(JobKind.SOURCE_INGEST)) == 600
    assert job_timeout_s(queue_for(JobKind.CAMPAIGN_PLANNING)) == 1800
    assert job_timeout_s(queue_for(JobKind.ASSET_GENERATION)) == 3600
    for kind in JobKind:
        # The reaper never fails a job that RQ could still legitimately be running.
        assert stuck_after(kind) > timedelta(seconds=job_timeout_s(queue_for(kind)))
