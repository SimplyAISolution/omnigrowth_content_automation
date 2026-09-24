"""Scheduler process loop (Phase 2 FOUNDATION): DB-down tick, timeout, heartbeat, stop."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import omnigrowth.scheduling.__main__ as sched
from omnigrowth.core.errors import ErrorCode, OmniGrowthError


@pytest.fixture(autouse=True)
def _heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "hb"
    monkeypatch.setattr(sched, "HEARTBEAT_FILE", path)

    async def no_reap() -> int:
        return 0

    monkeypatch.setattr(sched, "reap_stuck_jobs", no_reap)
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

    monkeypatch.setattr(sched, "ping_database", ok)
    monkeypatch.setattr(sched, "get_engine", lambda: _Engine())
    stop = asyncio.Event()
    task = asyncio.create_task(sched.run(stop, tick_seconds=0.01))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert calls >= 1
