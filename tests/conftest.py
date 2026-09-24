"""Shared test fixtures. Unit tests never reach a real DB, Redis or LLM."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator

import pytest

from omnigrowth.core.aio import loop_factory
from omnigrowth.core.settings import get_settings
from omnigrowth.storage import db

# Unroutable ports: anything that accidentally connects fails fast instead of hitting a
# real service (ADR-0008).
TEST_ENV = {
    "DATABASE_URL": "postgresql://og:og@127.0.0.1:1/og_test",
    "REDIS_URL": "redis://127.0.0.1:1/0",
    "ENV": "test",
    "PUBLISH_MODE": "MOCK",
    "LOG_JSON": "true",
    "OMNIGROWTH_RATE_LIMIT_BACKEND": "memory",
}


def _clear_caches() -> None:
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    _clear_caches()
    yield
    _clear_caches()
    root.handlers = saved_handlers
    root.setLevel(saved_level)


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    """psycopg async cannot use Windows' ProactorEventLoop (see omnigrowth.core.aio)."""
    return {"loop": loop_factory}
