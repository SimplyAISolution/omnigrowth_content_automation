"""OmniGrowth api.routes package: every v1 router is mounted on ``api_router``."""

from __future__ import annotations

from fastapi import APIRouter

from omnigrowth.api.routes import (
    analytics,
    approvals,
    audit,
    auth,
    brands,
    campaigns,
    content,
    health,
    jobs,
    platform_accounts,
    publications,
    sources,
)

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(brands.router)
api_router.include_router(sources.router)
api_router.include_router(campaigns.router)
api_router.include_router(content.router)
api_router.include_router(approvals.router)
api_router.include_router(publications.router)
api_router.include_router(platform_accounts.router)
api_router.include_router(analytics.router)
api_router.include_router(analytics.recommendations_router)
api_router.include_router(audit.router)
api_router.include_router(jobs.router)

__all__ = ["API_PREFIX", "api_router"]
