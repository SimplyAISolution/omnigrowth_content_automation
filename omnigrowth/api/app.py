"""FastAPI application factory (``uvicorn omnigrowth.api.app:create_app --factory``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from omnigrowth import __version__
from omnigrowth.api.body_limit import BodySizeLimitMiddleware
from omnigrowth.api.errors import install_exception_handlers
from omnigrowth.api.middleware import CorrelationIdMiddleware
from omnigrowth.api.routes import API_PREFIX, api_router
from omnigrowth.core.logging import configure_logging
from omnigrowth.core.settings import get_settings
from omnigrowth.security.ratelimit import MemoryBackend, RateLimiter, RedisBackend
from omnigrowth.storage import db


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    # Dispose only an engine that was actually created; never build one on shutdown.
    if db.get_engine.cache_info().currsize:
        await db.get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)

    app = FastAPI(
        title="OmniGrowth",
        version=__version__,
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    # Order: the body limit sits inside the correlation middleware so its 413 carries ids.
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    backend = (
        MemoryBackend()
        if settings.rate_limit_backend == "memory"
        else RedisBackend(settings.redis_url.get_secret_value())
    )
    app.state.rate_limiter = RateLimiter(backend)
    install_exception_handlers(app)
    app.include_router(api_router)
    return app
