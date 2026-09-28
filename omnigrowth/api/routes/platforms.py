"""``GET /api/v1/platforms`` — supported formats and publishing mode per platform (Phase 11).

Read-only view of ``config/qa.yaml`` and the writer registry, so clients present supported
formats without duplicating them. Phase 13: platforms with a VERIFIED live connector
(publishing/registry.py) report ``capability_status=VERIFIED``; all others stay UNVERIFIED
and simulated.
"""

from __future__ import annotations

from fastapi import APIRouter

from omnigrowth.agents import writers
from omnigrowth.api.deps import ViewerDep
from omnigrowth.content.qa import cached_qa_config
from omnigrowth.core.enums import Platform, PublishMode
from omnigrowth.core.settings import get_settings
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.platforms import PlatformCatalogRead, PlatformLimitsRead
from omnigrowth.services.connectors import has_live_connector

router = APIRouter(prefix="/platforms", tags=["platforms"])


@router.get("", response_model=Page[PlatformCatalogRead])
async def list_platforms(_principal: ViewerDep) -> Page[PlatformCatalogRead]:
    settings = get_settings()
    rules = cached_qa_config(settings.config_dir).platforms
    items = []
    for platform in Platform:
        rule = rules.get(platform)
        live = has_live_connector(platform)
        # MOCK mode simulates every platform; LIVE mode only reaches verified connectors
        # (per account, once enabled; the account view lists what still blocks it).
        publisher: PublishMode | None = (
            PublishMode.MOCK
            if settings.publish_mode is PublishMode.MOCK
            else (PublishMode.LIVE if live and settings.publish_mode is PublishMode.LIVE else None)
        )
        items.append(
            PlatformCatalogRead(
                platform=platform,
                has_writer=platform in writers.WRITERS and rule is not None,
                formats=list(rule.formats) if rule else [],
                limits=PlatformLimitsRead(
                    max_chars=rule.max_chars if rule else None,
                    max_chars_per_post=rule.max_chars_per_post if rule else None,
                    max_title_chars=rule.max_title_chars if rule else None,
                    max_thread_posts=rule.max_thread_posts if rule else None,
                    max_hashtags=rule.max_hashtags if rule else 0,
                ),
                capability_status="VERIFIED" if live else "UNVERIFIED",
                effective_publisher=publisher,
                live_publishing_available=live,
            )
        )
    return Page[PlatformCatalogRead](items=items)
