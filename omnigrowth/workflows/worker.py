"""RQ worker entrypoint: ``python -m omnigrowth.workflows.worker``.

Reads REDIS_URL from Settings (env / .env) so no shell interpolation is needed (ADR-0008:
explicit URLs). On native Windows, RQ's forking worker is unavailable, so a SimpleWorker
with timer-based timeouts is used (ADR-0003 Windows note).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from redis import Redis
from rq import Queue, SimpleWorker, Worker
from rq.timeouts import TimerDeathPenalty
from rq.worker import BaseWorker

from omnigrowth.core.logging import configure_logging
from omnigrowth.core.settings import get_settings

QUEUES: tuple[str, ...] = ("ingest", "agents", "generate", "qa", "embed", "analytics")

log = logging.getLogger("omnigrowth.worker")


class WindowsWorker(SimpleWorker):
    """No fork, no SIGALRM: job timeouts are enforced with a timer thread."""

    death_penalty_class = TimerDeathPenalty


def build_worker(queues: Sequence[str], *, windows: bool) -> BaseWorker:
    connection = Redis.from_url(get_settings().redis_url.get_secret_value())
    queue_objs = [Queue(name, connection=connection) for name in queues]
    worker_cls: type[BaseWorker] = WindowsWorker if windows else Worker
    return worker_cls(queue_objs, connection=connection)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="OmniGrowth RQ worker")
    parser.add_argument("queues", nargs="*", default=list(QUEUES))
    parser.add_argument("--burst", action="store_true", help="exit when queues are empty")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    unknown = sorted(set(args.queues) - set(QUEUES))
    if unknown:
        parser.error(f"unknown queues: {', '.join(unknown)}")
    worker = build_worker(args.queues, windows=sys.platform == "win32")
    log.info("worker starting", extra={"queues": list(args.queues), "burst": args.burst})
    worker.work(burst=args.burst, with_scheduler=False)


if __name__ == "__main__":
    main()
