"""Run a parser in a child process that is KILLED when it overruns its budget.

Why: the in-parser budget (``IngestionLimits.max_parse_seconds``) is cooperative — it is
checked between ZIP chunks and PDF pages, so one slow C-level step (lxml parsing a huge
``document.xml``, a pypdf filter) is not interrupted. RQ's job timeout does not help on
Windows either: ``TimerDeathPenalty`` raises an async exception that only lands between
Python bytecodes. A separate process can always be killed, on every OS.

Contract:
* the child is started with the ``spawn`` method (no forked copies of sockets/threads);
* the parent waits ``timeout_s`` for a result, then ``kill()``s the child and raises
  ``VALIDATION_FAILURE`` with ``details.limit = "max_parse_seconds"`` and ``killed = true``;
* on POSIX the child's address space is capped (``RLIMIT_AS``) so a decompression or XML
  bomb that slips past the byte checks dies with MemoryError instead of exhausting the host;
  Windows has no per-process rlimit here (documented in docs/audit/safeguards_post_mvp.md);
* a child that dies without answering (MemoryError, segfault, bug) is reported as
  ``INGESTION_FAILURE`` reason ``PARSER_CRASHED``; no broad ``except`` is needed for that.
"""

from __future__ import annotations

import importlib
import multiprocessing
import os
import sys
import threading
from multiprocessing.connection import Connection
from typing import Any

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.ingestion._common import ingestion_error
from omnigrowth.ingestion.normalized import NormalizedDocument

# Only these parser entry points may run isolated in production.
TARGETS: dict[str, str] = {
    "parse": "omnigrowth.ingestion.parsers:parse",
    "parse_html": "omnigrowth.ingestion.parsers.html_parser:parse_html",
}
CHILD_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
KILL_JOIN_TIMEOUT_S = 5.0


def _limit_memory() -> None:
    if sys.platform == "win32":
        return
    import resource  # POSIX only

    resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY_BYTES, CHILD_MEMORY_BYTES))


PARENT_POLL_S = 0.5


def _exit_with_parent() -> None:
    """Watchdog thread: if the parent dies (a SIGKILLed work-horse, a crashed worker), the
    child exits too instead of parsing on as an orphan."""
    parent = multiprocessing.parent_process()
    if parent is None:  # pragma: no cover - always started by run_target
        return

    def watch() -> None:
        parent.join()  # returns when the parent process ends
        os._exit(70)

    threading.Thread(target=watch, name="parent-watchdog", daemon=True).start()


def _child(conn: Connection, target: str, data: bytes, kwargs: dict[str, Any]) -> None:
    _exit_with_parent()
    _limit_memory()
    module_name, _, func_name = target.partition(":")
    func = getattr(importlib.import_module(module_name), func_name)
    try:
        doc = func(data, **kwargs)
    except OmniGrowthError as exc:
        conn.send(("error", exc.code.value, exc.message, exc.details, exc.retryable))
    else:
        conn.send(("ok", doc.model_dump(mode="json")))
    finally:
        conn.close()


def run_target(target: str, data: bytes, kwargs: dict[str, Any], *, timeout_s: float) -> Any:
    """Run ``module:function(data, **kwargs)`` in a killable child; return its payload."""
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_child, args=(sender, target, data, kwargs), daemon=True)
    process.start()
    sender.close()  # the parent keeps only the reading end: EOF when the child dies
    try:
        if not receiver.poll(timeout_s):
            process.kill()
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE,
                "ingestion limit exceeded: max_parse_seconds",
                details={"limit": "max_parse_seconds", "max": timeout_s, "killed": True},
            )
        try:
            message = receiver.recv()
        except EOFError as exc:
            process.join(KILL_JOIN_TIMEOUT_S)  # exitcode is only known once reaped
            raise ingestion_error(
                "the parser process died before finishing (memory limit or crash)",
                "PARSER_CRASHED",
                exit_code=process.exitcode,
            ) from exc
    finally:
        receiver.close()
        process.join(KILL_JOIN_TIMEOUT_S)
        if process.is_alive():
            process.kill()
            process.join(KILL_JOIN_TIMEOUT_S)
    if message[0] == "error":
        _, code, text, details, retryable = message
        raise OmniGrowthError(ErrorCode(code), text, details=details, retryable=retryable)
    return message[1]


def parse_isolated(
    entry: str, data: bytes, *, timeout_s: float, **kwargs: Any
) -> NormalizedDocument:
    """``parse`` / ``parse_html`` in a killable child process (worker-side ingestion)."""
    payload = run_target(TARGETS[entry], data, kwargs, timeout_s=timeout_s)
    return NormalizedDocument.model_validate(payload)
