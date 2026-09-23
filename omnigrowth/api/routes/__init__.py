"""OmniGrowth api.routes package: every v1 router is mounted on ``api_router``."""

from __future__ import annotations

from fastapi import APIRouter

from omnigrowth.api.routes import health

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(health.router)

__all__ = ["API_PREFIX", "api_router"]
