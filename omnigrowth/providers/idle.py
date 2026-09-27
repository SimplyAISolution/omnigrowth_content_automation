"""Server idleness verification before every local model call (Phase 9 item 2).

Measured on 2026-09-26 (scripts/probe_cancellation.py, docs/audit/live/cancellation_*.json):
when a client disconnects, LM Studio logs "Client disconnected. Stopping generation...",
cancels the task and releases the slot; the model status returns to ``idle`` within ~0.4 s
and GPU utilisation falls from ~93 % to ~2 % within ~1 s. The same log line warns that a
prompt already being processed is finished first, so stopping is fast but not instant, and
other clients of the server (it serves ``parallel`` requests) are outside OmniGrowth's lease.

Therefore a call starts only when BOTH hold: OmniGrowth's lease (providers/slots.py) and the
server's own report that every loaded model is ``idle`` with an empty queue, confirmed by
two consecutive samples. When idleness cannot be verified (CLI missing, server unreachable)
or does not arrive within the wait budget, the call is BLOCKED with MODEL_UNAVAILABLE — it
never starts on an assumption.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from omnigrowth.core.errors import ErrorCode, OmniGrowthError

POLL_S = 0.25
CONFIRMATIONS = 2


class IdleProbe(Protocol):
    async def wait_idle(self, *, timeout_s: float) -> dict[str, Any]: ...


def _blocked(reason: str, **details: object) -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.MODEL_UNAVAILABLE,
        "the model server is not verifiably idle; the call is blocked until it is",
        details={"reason": reason, **details},
        retryable=True,
    )


def default_lms_path() -> str | None:
    found = shutil.which("lms")
    if found:
        return found
    candidate = (
        Path.home() / ".lmstudio" / "bin" / ("lms.exe" if sys.platform == "win32" else "lms")
    )
    return str(candidate) if candidate.exists() else None


class LMStudioCliIdleProbe:
    """Idle = every loaded model reports status ``idle`` and ``queued == 0`` in
    ``lms ps --json``, for ``CONFIRMATIONS`` consecutive samples."""

    def __init__(
        self,
        lms_path: str | None = None,
        *,
        runner: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._lms = lms_path if lms_path is not None else default_lms_path()
        self._runner = runner or self._ps
        self._clock = clock
        self._sleep = sleep

    async def _ps(self) -> list[dict[str, Any]]:
        if self._lms is None:
            raise _blocked("IDLENESS_UNVERIFIABLE", cause="lms CLI not found")
        try:
            out = await asyncio.to_thread(
                subprocess.run,
                [self._lms, "ps", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            models = json.loads(out.stdout or "[]")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise _blocked("IDLENESS_UNVERIFIABLE", cause=type(exc).__name__) from exc
        return list(models)

    async def wait_idle(self, *, timeout_s: float) -> dict[str, Any]:
        deadline = self._clock() + timeout_s
        streak = 0
        last: list[dict[str, Any]] = []
        while True:
            last = await self._runner()
            busy = [
                m.get("identifier")
                for m in last
                if m.get("status") != "idle" or int(m.get("queued") or 0) > 0
            ]
            streak = 0 if busy else streak + 1
            if streak >= CONFIRMATIONS:
                return {"idle": True, "models": [m.get("identifier") for m in last]}
            if self._clock() >= deadline:
                raise _blocked(
                    "SERVER_BUSY",
                    busy=busy,
                    wait_s=timeout_s,
                    statuses={m.get("identifier"): m.get("status") for m in last},
                )
            await self._sleep(POLL_S)
