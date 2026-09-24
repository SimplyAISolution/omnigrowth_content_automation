"""OmniGrowth api.routes package: every v1 router is mounted on ``api_router``."""

from __future__ import annotations

from fastapi import APIRouter

from omnigrowth.api.routes import auth, brands, campaigns, health, jobs, sources

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(brands.router)
api_router.include_router(sources.router)
api_router.include_router(campaigns.router)
api_router.include_router(jobs.router)

__all__ = ["API_PREFIX", "api_router"]
