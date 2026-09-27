"""Platform catalog schema (Phase 11): what the product can write and how it publishes."""

from __future__ import annotations

from pydantic import BaseModel, Field

from omnigrowth.core.enums import ContentFormat, Platform, PublishMode


class PlatformLimitsRead(BaseModel):
    """QA limits from ``config/qa.yaml``. All are UNVERIFIED (PLATFORM_CAPABILITIES.md)."""

    max_chars: int | None = None
    max_chars_per_post: int | None = None
    max_title_chars: int | None = None
    max_thread_posts: int | None = None
    max_hashtags: int


class PlatformCatalogRead(BaseModel):
    platform: Platform
    has_writer: bool
    formats: list[ContentFormat] = Field(description="Formats the writer and QA support.")
    limits: PlatformLimitsRead
    capability_status: str = Field(
        description="Verification of the platform's real API; UNVERIFIED until Phase 13."
    )
    effective_publisher: PublishMode | None = Field(
        description="MOCK = simulated publication only; null = publishing is refused."
    )
    live_publishing_available: bool
