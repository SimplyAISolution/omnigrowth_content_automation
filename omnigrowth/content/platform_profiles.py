"""Per-account platform settings (docs/PLATFORM_CAPABILITIES.md §4.6, §4.9).

Stored as validated JSON in ``platform_accounts.settings``. Only REDDIT (one profile per
subreddit, spec §8 "per-subreddit configuration") and MASTODON (per-instance limits) have
settings today; every other platform must keep ``settings`` empty. The values are
human-maintained snapshots: OmniGrowth never fetches them from a platform (AGENTS.md: no
network calls to social platforms).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from omnigrowth.core.enums import Platform
from omnigrowth.core.errors import ErrorCode, OmniGrowthError

# A subreddit profile older than this is reported as stale by QA (warning only).
PROFILE_STALE_AFTER = timedelta(days=90)


def normalize_subreddit(name: str) -> str:
    """``r/LocalLLaMA``, ``/r/LocalLLaMA`` and ``LocalLLaMA`` name the same subreddit."""
    name = name.strip()
    for prefix in ("/r/", "r/", "/R/", "R/"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


class SubredditProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subreddit: str = Field(min_length=2, max_length=50, pattern=r"^[A-Za-z0-9_]+$")
    rules_summary: str | None = Field(default=None, max_length=4000)
    self_promotion_policy: Literal["ALLOWED", "RESTRICTED", "BANNED"] = "RESTRICTED"
    text_posts_allowed: bool = True
    link_posts_allowed: bool = True
    flair_required: bool = False
    allowed_flairs: list[str] = Field(default_factory=list, max_length=50)
    title_prefix: str | None = Field(default=None, max_length=40)
    max_title_chars: int | None = Field(default=None, ge=10, le=300)
    disclosure_required: bool = False
    banned_phrases: list[str] = Field(default_factory=list, max_length=200)
    last_reviewed_at: datetime | None = None
    reviewed_by: str | None = Field(default=None, max_length=200)

    @field_validator("subreddit", mode="before")
    @classmethod
    def _strip_prefix(cls, value: object) -> object:
        return normalize_subreddit(value) if isinstance(value, str) else value

    def stale(self, now: datetime | None = None) -> bool:
        if self.last_reviewed_at is None:
            return True
        reviewed = self.last_reviewed_at
        if reviewed.tzinfo is None:
            reviewed = reviewed.replace(tzinfo=UTC)
        return (now or datetime.now(UTC)) - reviewed > PROFILE_STALE_AFTER


class RedditSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subreddits: list[SubredditProfile] = Field(default_factory=list, max_length=50)

    @field_validator("subreddits")
    @classmethod
    def _unique(cls, value: list[SubredditProfile]) -> list[SubredditProfile]:
        names = [p.subreddit.casefold() for p in value]
        if len(names) != len(set(names)):
            raise ValueError("each subreddit may be configured once per account")
        return value


class MastodonSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # https://<instance>, no path/query (the live connector calls <base>/api/v1/...).
    instance_base_url: str | None = Field(default=None, max_length=300)
    # Per-instance post length (instance config; default 500 in config/qa.yaml).
    max_characters: int | None = Field(default=None, ge=100, le=100_000)
    # Every URL counts as this many characters (instance config, VERIFIED field, default 23).
    characters_reserved_per_url: int = Field(default=23, ge=1, le=500)
    # Who sees live posts: public / unlisted / private (followers only). Never "direct".
    visibility: Literal["public", "unlisted", "private"] = "public"
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    instance_rules: str | None = Field(default=None, max_length=4000)

    @field_validator("instance_base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise ValueError("instance_base_url must be an absolute URL")
        if parts.path.strip("/") or parts.query or parts.fragment or parts.username:
            raise ValueError("instance_base_url must be only scheme://host[:port]")
        # http is only reachable for hosts in OMNIGROWTH_PLATFORM_HOST_ALLOWLIST (sandbox).
        return f"{parts.scheme}://{parts.netloc.lower()}"


_SCHEMAS: dict[Platform, type[BaseModel]] = {
    Platform.REDDIT: RedditSettings,
    Platform.MASTODON: MastodonSettings,
}


def validate_settings(platform: Platform, raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalised settings JSON for ``platform``; VALIDATION_FAILURE when malformed."""
    if not raw:
        return {}
    schema = _SCHEMAS.get(platform)
    if schema is None:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            f"{platform.value} accounts have no settings",
            details={"platform": platform.value},
        )
    try:
        return schema.model_validate(dict(raw)).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            f"invalid {platform.value} account settings",
            details={
                "platform": platform.value,
                "errors": [
                    {"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()
                ],
            },
        ) from exc


def subreddit_profiles(settings: Iterable[Mapping[str, Any]]) -> tuple[SubredditProfile, ...]:
    """All subreddit profiles across a brand's REDDIT accounts (first one wins per name)."""
    seen: dict[str, SubredditProfile] = {}
    for raw in settings:
        for profile in RedditSettings.model_validate(dict(raw or {})).subreddits:
            seen.setdefault(profile.subreddit.casefold(), profile)
    return tuple(seen.values())


def mastodon_max_characters(settings: Iterable[Mapping[str, Any]]) -> int | None:
    """The strictest configured instance limit across a brand's MASTODON accounts."""
    limits = [
        s.max_characters
        for s in (MastodonSettings.model_validate(dict(raw or {})) for raw in settings)
        if s.max_characters is not None
    ]
    return min(limits) if limits else None
