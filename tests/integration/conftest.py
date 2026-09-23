"""Integration fixtures: a throw-away database migrated with Alembic, per test session.

The real server URL comes from ``INTEGRATION_DATABASE_URL`` / ``DATABASE_URL`` in the
process environment (CI) or from the repo's ``.env`` (local). A fresh database
``og_it_<random>`` is created on that server, migrated to head, and dropped afterwards, so
integration tests never touch the developer's data.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from omnigrowth.storage.db import to_async_url

REPO = Path(__file__).resolve().parents[2]


def _from_dotenv(key: str) -> str | None:
    env_file = REPO / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.split("#", 1)[0].strip() or None
    return None


# Captured at import time, before the root autouse fixture swaps in unroutable URLs.
_SERVER_URL = (
    os.environ.get("INTEGRATION_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or _from_dotenv("DATABASE_URL")
)
_REDIS_URL = os.environ.get("REDIS_URL") or _from_dotenv("REDIS_URL")


def _plain(url: str) -> str:
    """psycopg wants postgresql://, not SQLAlchemy dialect prefixes."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _with_database(url: str, database: str) -> str:
    return make_url(_plain(url)).set(database=database).render_as_string(hide_password=False)


def run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": database_url, "ENV": "test", "PUBLISH_MODE": "MOCK"}
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="session")
def server_url() -> str:
    if not _SERVER_URL or ":1/" in _SERVER_URL:
        pytest.skip("no integration database configured (DATABASE_URL / .env)")
    return _SERVER_URL


@pytest.fixture(scope="session")
def fresh_database(server_url: str) -> Iterator[str]:
    """Create an empty database, run `alembic upgrade head`, drop it at session end."""
    name = f"og_it_{secrets.token_hex(4)}"
    admin = _with_database(server_url, "postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = _with_database(server_url, name)
    try:
        result = run_alembic(url, "upgrade", "head")
        assert result.returncode == 0, result.stderr[-2000:]
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(autouse=True)
def _integration_env(
    _test_env: None, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Point settings at the integration database / Redis (overrides the unit-test env)."""
    if "fresh_database" in request.fixturenames:
        monkeypatch.setenv("DATABASE_URL", request.getfixturevalue("fresh_database"))
    if _REDIS_URL:
        monkeypatch.setenv("REDIS_URL", _REDIS_URL)


@pytest.fixture
async def engine(fresh_database: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(to_async_url(fresh_database))
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Session inside a transaction that is always rolled back."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        sess = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield sess
        finally:
            await sess.close()
            await trans.rollback()
