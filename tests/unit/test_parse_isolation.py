"""Isolated parsing (ingestion/isolation.py): the parse budget is a HARD stop.

A parser stuck in one step that never checks the budget is killed at ``timeout_s``; a child
that dies is reported, not hung on; results and typed errors round-trip unchanged.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.ingestion import parse
from omnigrowth.ingestion.isolation import parse_isolated, run_target
from tests.unit.test_ingestion_parsers import make_docx

TEXT = b"Local agents keep data on the machine.\nThey avoid per-token fees.\n"


def test_isolated_parse_matches_in_process_parse() -> None:
    for data, name in ((TEXT, "note.txt"), (make_docx(), "doc.docx")):
        isolated = parse_isolated("parse", data, timeout_s=60, filename=name)
        assert isolated == parse(data, filename=name)


def test_typed_errors_cross_the_process_boundary() -> None:
    with pytest.raises(OmniGrowthError) as info:
        parse_isolated("parse", b"\x00" * 64, timeout_s=60, filename="x.txt")
    assert info.value.code is ErrorCode.INGESTION_FAILURE
    assert info.value.message == "not a text file"


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFO
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_a_parser_stuck_in_one_step_is_killed_at_the_budget(tmp_path: Path) -> None:
    marker = tmp_path / "pid.txt"
    started = time.monotonic()
    with pytest.raises(OmniGrowthError) as info:
        run_target(
            "tests.unit.isolation_targets:hang",
            b"",
            {"marker": str(marker)},
            timeout_s=5,  # spawn start-up measured at ~0.4 s on Windows
        )
    elapsed = time.monotonic() - started
    assert info.value.details == {"limit": "max_parse_seconds", "max": 5, "killed": True}
    assert elapsed < 5 + 6  # budget + join allowance, never the child's 600 s
    pid = int(marker.read_text(encoding="utf-8"))
    assert not _alive(pid)


def test_a_child_that_dies_is_reported_not_waited_for() -> None:
    with pytest.raises(OmniGrowthError) as info:
        run_target("tests.unit.isolation_targets:die", b"", {}, timeout_s=60)
    assert info.value.code is ErrorCode.INGESTION_FAILURE
    assert info.value.details["reason"] == "PARSER_CRASHED"
    assert info.value.details["exit_code"] == 3


def test_the_parse_child_exits_when_its_parent_dies(tmp_path: Path) -> None:
    """Regression (reviewer m16): the parent (a plain process, like an RQ work-horse) starts
    a parse child and is then killed abruptly; the child must not live on as an orphan."""
    import subprocess
    import sys

    marker = tmp_path / "grandchild.pid"
    code = (
        "from tests.unit.isolation_targets import spawn_grandchild; "
        f"spawn_grandchild(b'', marker={str(marker)!r})"
    )
    parent = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2], timeout=60
    )
    assert parent.returncode == 9  # died abruptly
    pid = int(marker.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(pid)


def test_retryable_flag_survives_the_process_boundary() -> None:
    from omnigrowth.ingestion.isolation import run_target as run

    with pytest.raises(OmniGrowthError) as info:
        run("tests.unit.isolation_targets:retryable_error", b"", {}, timeout_s=60)
    assert info.value.retryable is True and info.value.code is ErrorCode.MODEL_TIMEOUT
