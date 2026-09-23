"""Event-loop helpers.

psycopg's async mode cannot run on Windows' default ``ProactorEventLoop`` (it raises
``InterfaceError``), so every OmniGrowth entrypoint that runs natively on Windows must use a
selector loop. Linux containers are unaffected.
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from collections.abc import Callable, Coroutine
from typing import Any


def loop_factory() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()


def run[T](main: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """``asyncio.run`` with a psycopg-compatible loop on every platform."""
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        return runner.run(main())
