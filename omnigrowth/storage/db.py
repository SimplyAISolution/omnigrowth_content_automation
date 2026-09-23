"""Async engine / session factory (ADR-0001: SQLAlchemy 2 async + psycopg 3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.settings import get_settings


def to_async_url(url: str) -> str:
    """Normalise any postgres URL to the psycopg (v3) async dialect."""
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    raise OmniGrowthError(ErrorCode.CONFIGURATION_ERROR, "DATABASE_URL must be a postgresql:// URL")


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        to_async_url(settings.database_url.get_secret_value()),
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def session_scope() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request; commit is explicit in services."""
    async with get_sessionmaker()() as session:
        yield session


async def ping_database(engine: AsyncEngine | None = None) -> None:
    """Raise DATABASE_FAILURE if the database is unreachable."""
    try:
        async with (engine or get_engine()).connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        raise OmniGrowthError(
            ErrorCode.DATABASE_FAILURE,
            "database unreachable",
            details={"error": type(exc).__name__},
        ) from exc
