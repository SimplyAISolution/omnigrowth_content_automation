"""Scheduler process entrypoint: ``python -m omnigrowth.scheduling``.

Phase 2 (FOUNDATION): runs the process loop with structured logging, a bounded DB liveness
check and a heartbeat file (used by the compose healthcheck) on every tick. The outbox
claimer and dispatcher (A3, STATE_MACHINES §3.4) are registered in Phase 7.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import tempfile
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from omnigrowth.core import aio
from omnigrowth.core.errors import OmniGrowthError
from omnigrowth.core.ids import new_correlation_id
from omnigrowth.core.logging import bind_context, configure_logging
from omnigrowth.core.settings import get_settings
from omnigrowth.storage.db import get_engine, ping_database
from omnigrowth.workflows.tasks import reap_stuck_jobs

log = logging.getLogger("omnigrowth.scheduler")

TICK_SECONDS = 5.0
DB_PING_TIMEOUT_S = 3.0
HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "omnigrowth-scheduler.heartbeat"


async def tick() -> bool:
    """One scheduler tick. Returns True when the database answered in time."""
    try:
        async with asyncio.timeout(DB_PING_TIMEOUT_S):
            await ping_database()
    except OmniGrowthError as exc:
        log.error("scheduler tick: database unavailable", extra={"code": str(exc.code)})
        return False
    except TimeoutError:
        log.error("scheduler tick: database ping timed out", extra={"timeout_s": DB_PING_TIMEOUT_S})
        return False
    try:
        reaped = await reap_stuck_jobs()
    except (OmniGrowthError, SQLAlchemyError) as exc:
        log.error("scheduler tick: reaper failed", extra={"error": type(exc).__name__})
    else:
        if reaped:
            log.warning("scheduler tick: reaped stuck jobs", extra={"count": reaped})
    HEARTBEAT_FILE.touch()
    return True


async def run(stop: asyncio.Event, *, tick_seconds: float = TICK_SECONDS) -> None:
    log.info("scheduler started", extra={"tick_seconds": tick_seconds})
    try:
        while not stop.is_set():
            with bind_context(correlation_id=new_correlation_id()):
                await tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=tick_seconds)
            except TimeoutError:
                continue
    finally:
        await get_engine().dispose()
        log.info("scheduler stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    stop = asyncio.Event()

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows event loop: Ctrl+C raises KeyboardInterrupt
                log.debug("signal handlers unavailable on this platform")
        await run(stop)

    aio.run(_main)


if __name__ == "__main__":
    main()
