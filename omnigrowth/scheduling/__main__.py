"""Scheduler process entrypoint: ``python -m omnigrowth.scheduling``.

Every tick: bounded DB liveness check, stuck-job reaper, then the publication outbox
(A3, STATE_MACHINES §3.4: lease recovery, claim with SKIP LOCKED, dispatch) and a heartbeat
file for the compose healthcheck. Only the MockPublisher exists before Phase 13; any other
PUBLISH_MODE makes the dispatcher refuse (never a silent live post).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from omnigrowth.core import aio
from omnigrowth.core.errors import OmniGrowthError
from omnigrowth.core.ids import new_correlation_id
from omnigrowth.core.logging import bind_context, configure_logging
from omnigrowth.core.settings import get_settings
from omnigrowth.publishing.base import PlatformPublisher
from omnigrowth.publishing.config import cached_publishing_config
from omnigrowth.publishing.mock import MockPublisher
from omnigrowth.publishing.scheduler import run_once
from omnigrowth.services.idempotency import purge_expired as purge_idempotency_keys
from omnigrowth.storage.db import get_engine, ping_database
from omnigrowth.workflows.tasks import reap_stuck_jobs

log = logging.getLogger("omnigrowth.scheduler")

TICK_SECONDS = 5.0
DB_PING_TIMEOUT_S = 3.0
HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "omnigrowth-scheduler.heartbeat"


SCHEDULER_ID = f"{socket.gethostname()}:{os.getpid()}"


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
    try:
        # Idempotency keys live 24 h (API_PLAN §1.6); expired rows are the one hard-deleted
        # class besides superseded embeddings (DATA_MODEL §6a.2).
        await purge_idempotency_keys()
    except (OmniGrowthError, SQLAlchemyError) as exc:
        log.error("scheduler tick: key purge failed", extra={"error": type(exc).__name__})
    try:
        # A fresh mock per tick: it keeps no state worth carrying (its idempotency comes
        # from the key), and a long-lived one would grow without bound.
        publisher: PlatformPublisher = MockPublisher()
        outbox = await run_once(
            publisher=publisher,
            config=cached_publishing_config(get_settings().config_dir),
            now=datetime.now(UTC),
            scheduler_id=SCHEDULER_ID,
        )
    except (OmniGrowthError, SQLAlchemyError) as exc:
        log.error("scheduler tick: outbox failed", extra={"error": type(exc).__name__})
    else:
        if outbox.claimed or outbox.recovered:
            log.info(
                "scheduler tick: outbox",
                extra={
                    "claimed": outbox.claimed,
                    "succeeded": outbox.succeeded,
                    "retry_wait": outbox.retry_wait,
                    "failed": outbox.failed,
                    "uncertain": outbox.uncertain,
                    "held": outbox.held,
                    "recovered": outbox.recovered,
                },
            )
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
        tick = cached_publishing_config(settings.config_dir).scheduler.tick_seconds
        await run(stop, tick_seconds=tick)

    aio.run(_main)


if __name__ == "__main__":
    main()
