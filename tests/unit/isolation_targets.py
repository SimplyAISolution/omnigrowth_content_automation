"""Child-process targets for tests/unit/test_parse_isolation.py (importable by ``spawn``)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


def hang(data: bytes, *, marker: str) -> Any:
    """Simulates one C-level step that never checks a budget: writes its pid, then blocks."""
    Path(marker).write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(600)


def die(data: bytes) -> Any:
    """Simulates a parser killed by the OS (memory limit, segfault)."""
    os._exit(3)


def spawn_grandchild(data: bytes, *, marker: str) -> Any:
    """Runs (in a plain subprocess) as the PARENT of a hanging parse child, then dies
    abruptly like a SIGKILLed work-horse. The parse child must notice and exit."""
    import threading

    from omnigrowth.ingestion.isolation import run_target

    threading.Thread(
        target=run_target,
        args=("tests.unit.isolation_targets:hang", b"", {"marker": marker}),
        kwargs={"timeout_s": 600},
        daemon=True,
    ).start()
    deadline = time.monotonic() + 30
    while not Path(marker).exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    os._exit(9)


def retryable_error(data: bytes) -> Any:
    from omnigrowth.core.errors import ErrorCode, OmniGrowthError

    raise OmniGrowthError(ErrorCode.MODEL_TIMEOUT, "transient", retryable=True)
