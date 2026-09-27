"""Platform writer agents (§7E, §8): one prompt and one schema per platform.

Each schema's ``to_fields`` maps model output to ``content_versions`` columns
(``VersionFields``); ``build_draft`` composes the exact text that would be published (the QA
``Draft``) from those columns, for generated and human-edited versions alike.

Column conventions shared by every platform:

- ``body`` is the text that is published as-is: the post, caption, video description,
  Reddit self-post or newsletter. Multi-post platforms (X, Mastodon) store their posts in
  one body separated by lines containing only ``---``.
- ``title`` is published separately (YouTube, Rumble, Reddit, Substack).
- Everything else a platform needs (scenes, slides, script sections, chapters, title
  candidates, thumbnail concepts, subreddit, content warning, ...) lives in
  ``platform_payload`` next to ``hashtags`` and ``cited_claims`` and is described by that
  platform's ``*Extras`` model, which also validates human edits of those fields.

Length limits are deliberately NOT part of the schemas: an over-long post must reach QA and
be rejected there (§11 stage 4), not be silently repaired.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from omnigrowth.agents.base import AgentSpec
from omnigrowth.content.platform_profiles import normalize_subreddit
from omnigrowth.content.qa import Draft
from omnigrowth.core.enums import ContentFormat, ModelRole, Platform
from omnigrowth.core.errors import ErrorCode, OmniGrowthError

# One local model for planning and writing keeps a 12 GB GPU from swapping models between
# calls (config/models.yaml); switch to FRONTIER_CREATIVE once a second model fits.
WRITER_ROLE = ModelRole.REASONING_LOCAL

# platform_payload keys every platform shares; the rest belong to the platform's extras.
COMMON_PAYLOAD_KEYS = frozenset({"hashtags", "cited_claims"})


@dataclass(frozen=True, slots=True)
class VersionFields:
    """The ``content_versions`` columns a writer (or a human edit) produces."""

    hook: str
    body: str
    cta: str | None
    hashtags: list[str]
    cited_claims: list[int] = field(default_factory=list)
    hook_type: str | None = None
    cta_type: str | None = None
    media_requirements: list[str] = field(default_factory=list)
    title: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def payload(self) -> dict[str, Any]:
        return {**self.extras, "hashtags": self.hashtags, "cited_claims": self.cited_claims}


class WriterOutput(Protocol):
    def to_fields(self) -> VersionFields: ...


def _clean(text: str | None) -> str | None:
    return text.strip() if text and text.strip() else None


# ------------------------------------------------------------------ extras (platform_payload)
class _Extras(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Frame(BaseModel):
    """One Reel scene, carousel slide, Story frame or image."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    visual: str = Field(min_length=1, max_length=600)
    text: str = Field(default="", max_length=600)  # on-screen text / voice-over


class Section(BaseModel):
    """One part of a video outline or script."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    heading: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=6000)


class Chapter(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    at: str = Field(pattern=r"^(\d{1,2}:)?\d{1,2}:\d{2}$")
    title: str = Field(min_length=1, max_length=100)


class InstagramExtras(_Extras):
    visual_direction: str | None = Field(default=None, max_length=1000)
    frames: list[Frame] = Field(default_factory=list, max_length=25)


class YouTubeExtras(_Extras):
    # TITLE STRATEGY (§8) is kept apart from the script: candidates plus the chosen title.
    title_candidates: list[str] = Field(default_factory=list, max_length=5)
    sections: list[Section] = Field(default_factory=list, max_length=30)
    chapters: list[Chapter] = Field(default_factory=list, max_length=30)
    thumbnail_concepts: list[str] = Field(default_factory=list, max_length=5)
    pinned_comment: str | None = Field(default=None, max_length=1000)


class RumbleExtras(_Extras):
    title_candidates: list[str] = Field(default_factory=list, max_length=5)
    sections: list[Section] = Field(default_factory=list, max_length=30)
    thumbnail_concepts: list[str] = Field(default_factory=list, max_length=5)


class RedditExtras(_Extras):
    subreddit: str | None = Field(default=None, max_length=60)
    flair: str | None = Field(default=None, max_length=100)

    @field_validator("subreddit")
    @classmethod
    def _normalize(cls, value: str | None) -> str | None:
        return normalize_subreddit(value) or None if value else None


class SubstackExtras(_Extras):
    subtitle: str | None = Field(default=None, max_length=300)
    lead_magnet: str | None = Field(default=None, max_length=400)


class MastodonExtras(_Extras):
    content_warning: str | None = Field(default=None, max_length=200)


EXTRAS: dict[Platform, type[_Extras]] = {
    Platform.INSTAGRAM: InstagramExtras,
    Platform.YOUTUBE: YouTubeExtras,
    Platform.RUMBLE: RumbleExtras,
    Platform.REDDIT: RedditExtras,
    Platform.SUBSTACK: SubstackExtras,
    Platform.MASTODON: MastodonExtras,
}


def _extras_of(model: BaseModel, schema: type[_Extras]) -> dict[str, Any]:
    raw = model.model_dump(include=set(schema.model_fields))
    return schema.model_validate(raw).model_dump(mode="json", exclude_none=True)


def extras_from_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (payload or {}).items() if k not in COMMON_PAYLOAD_KEYS}


def merge_extras(
    platform: Platform, base: Mapping[str, Any] | None, changes: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The base version's extras with a human's changes applied (given keys replace)."""
    merged = {**extras_from_payload(base), **(changes or {})}
    schema = EXTRAS.get(platform)
    if schema is None:
        if changes:
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE,
                f"{platform.value} content has no platform fields",
                details={"platform": platform.value, "fields": sorted(changes)},
            )
        return merged
    try:
        return schema.model_validate(merged).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            f"invalid {platform.value} platform fields",
            details={
                "platform": platform.value,
                "errors": [
                    {"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()
                ],
            },
        ) from exc


# ------------------------------------------------------------------ writer schemas
class LinkedInPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook: str = Field(min_length=5, max_length=600)
    body: str = Field(min_length=20, max_length=12_000)
    cta: str | None = Field(default=None, max_length=400)
    hashtags: list[str] = Field(default_factory=list, max_length=10)
    cited_claims: list[int] = Field(default_factory=list, max_length=10)
    hook_type: str | None = Field(default=None, max_length=40)
    cta_type: str | None = Field(default=None, max_length=40)
    media_suggestion: str | None = Field(default=None, max_length=400)

    def to_fields(self) -> VersionFields:
        return VersionFields(
            hook=self.hook.strip(),
            body=self.body.strip(),
            cta=_clean(self.cta),
            hashtags=normalize_hashtags(self.hashtags),
            cited_claims=list(self.cited_claims),
            hook_type=self.hook_type,
            cta_type=self.cta_type,
            media_requirements=[self.media_suggestion] if self.media_suggestion else [],
        )


class FacebookPost(LinkedInPost):
    """Same shape as a LinkedIn post; the prompt carries Facebook's conventions."""


class XPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posts: list[str] = Field(min_length=1, max_length=25)
    cta: str | None = Field(default=None, max_length=400)
    hashtags: list[str] = Field(default_factory=list, max_length=5)
    cited_claims: list[int] = Field(default_factory=list, max_length=10)
    hook_type: str | None = Field(default=None, max_length=40)
    cta_type: str | None = Field(default=None, max_length=40)

    def to_fields(self) -> VersionFields:
        return self._thread_fields({})

    def _thread_fields(self, extras: dict[str, Any]) -> VersionFields:
        posts = [p.strip() for p in self.posts if p.strip()]
        return VersionFields(
            hook=posts[0].split("\n", 1)[0].strip() if posts else "",
            body=POST_SEPARATOR.join(posts),
            cta=_clean(self.cta),
            hashtags=normalize_hashtags(self.hashtags),
            cited_claims=list(self.cited_claims),
            hook_type=self.hook_type,
            cta_type=self.cta_type,
            extras=extras,
        )


class MastodonPost(XPost):
    content_warning: str | None = Field(default=None, max_length=200)

    def to_fields(self) -> VersionFields:
        return self._thread_fields(_extras_of(self, MastodonExtras))


class InstagramPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook: str = Field(min_length=5, max_length=300)
    caption: str = Field(min_length=20, max_length=6000)
    cta: str | None = Field(default=None, max_length=400)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    cited_claims: list[int] = Field(default_factory=list, max_length=10)
    hook_type: str | None = Field(default=None, max_length=40)
    cta_type: str | None = Field(default=None, max_length=40)
    visual_direction: str | None = Field(default=None, max_length=1000)
    frames: list[Frame] = Field(default_factory=list, max_length=25)

    def to_fields(self) -> VersionFields:
        return VersionFields(
            hook=self.hook.strip(),
            body=self.caption.strip(),
            cta=_clean(self.cta),
            hashtags=normalize_hashtags(self.hashtags),
            cited_claims=list(self.cited_claims),
            hook_type=self.hook_type,
            cta_type=self.cta_type,
            media_requirements=[self.visual_direction] if self.visual_direction else [],
            extras=_extras_of(self, InstagramExtras),
        )


class _Video(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_candidates: list[str] = Field(min_length=2, max_length=5)
    title: str = Field(min_length=3, max_length=200)
    hook: str = Field(min_length=5, max_length=600)  # the spoken opening
    sections: list[Section] = Field(min_length=1, max_length=30)
    description: str = Field(min_length=20, max_length=8000)
    thumbnail_concepts: list[str] = Field(min_length=1, max_length=5)
    cta: str | None = Field(default=None, max_length=400)
    hashtags: list[str] = Field(default_factory=list, max_length=5)
    cited_claims: list[int] = Field(default_factory=list, max_length=10)
    hook_type: str | None = Field(default=None, max_length=40)
    cta_type: str | None = Field(default=None, max_length=40)

    def _video_fields(self, extras: dict[str, Any]) -> VersionFields:
        return VersionFields(
            hook=self.hook.strip(),
            body=self.description.strip(),
            cta=_clean(self.cta),
            hashtags=normalize_hashtags(self.hashtags),
            cited_claims=list(self.cited_claims),
            hook_type=self.hook_type,
            cta_type=self.cta_type,
            media_requirements=["video (produced by a human from the script)", "thumbnail"],
            title=self.title.strip(),
            extras=extras,
        )


class YouTubeVideo(_Video):
    chapters: list[Chapter] = Field(default_factory=list, max_length=30)
    pinned_comment: str | None = Field(default=None, max_length=1000)

    def to_fields(self) -> VersionFields:
        return self._video_fields(_extras_of(self, YouTubeExtras))


class RumbleVideo(_Video):
    def to_fields(self) -> VersionFields:
        return self._video_fields(_extras_of(self, RumbleExtras))


class RedditPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subreddit: str | None = Field(default=None, max_length=60)
    title: str = Field(min_length=5, max_length=350)
    body: str = Field(min_length=50, max_length=40_000)
    flair: str | None = Field(default=None, max_length=100)
    cta: str | None = Field(default=None, max_length=400)
    cited_claims: list[int] = Field(default_factory=list, max_length=10)
    hook_type: str | None = Field(default=None, max_length=40)

    def to_fields(self) -> VersionFields:
        extras = _extras_of(self, RedditExtras)
        cta = _clean(self.cta)
        return VersionFields(
            hook=self.title.strip(),  # on Reddit the title is what people see first
            body=self.body.strip(),
            cta=cta,
            hashtags=[],
            cited_claims=list(self.cited_claims),
            hook_type=self.hook_type,
            cta_type="comment" if cta else "none",
            title=self.title.strip(),
            extras=extras,
        )


class SubstackPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=300)
    subtitle: str | None = Field(default=None, max_length=300)
    hook: str = Field(min_length=5, max_length=1000)  # the opening paragraph
    body: str = Field(min_length=20, max_length=60_000)
    cta: str | None = Field(default=None, max_length=400)
    lead_magnet: str | None = Field(default=None, max_length=400)
    cited_claims: list[int] = Field(default_factory=list, max_length=10)
    hook_type: str | None = Field(default=None, max_length=40)
    cta_type: str | None = Field(default=None, max_length=40)

    def to_fields(self) -> VersionFields:
        return VersionFields(
            hook=self.hook.strip(),
            body=self.body.strip(),
            cta=_clean(self.cta),
            hashtags=[],
            cited_claims=list(self.cited_claims),
            hook_type=self.hook_type,
            cta_type=self.cta_type,
            title=_clean(self.title),
            extras=_extras_of(self, SubstackExtras),
        )


def _spec(platform: str, schema: type[BaseModel]) -> AgentSpec[Any]:
    return AgentSpec(
        name=f"{platform}_writer",
        prompt_ref=f"content/{platform}_writer@v1",
        schema=schema,
        role=WRITER_ROLE,
    )


LINKEDIN = _spec("linkedin", LinkedInPost)
X = _spec("x", XPost)
WRITERS: dict[Platform, AgentSpec[Any]] = {
    Platform.LINKEDIN: LINKEDIN,
    Platform.X: X,
    Platform.YOUTUBE: _spec("youtube", YouTubeVideo),
    Platform.INSTAGRAM: _spec("instagram", InstagramPost),
    Platform.FACEBOOK: _spec("facebook", FacebookPost),
    Platform.REDDIT: _spec("reddit", RedditPost),
    Platform.SUBSTACK: _spec("substack", SubstackPost),
    Platform.RUMBLE: _spec("rumble", RumbleVideo),
    Platform.MASTODON: _spec("mastodon", MastodonPost),
}


def variables(
    *,
    brand: str,
    strategy: str,
    plan_item: str,
    idea: str,
    claims: str,
    source: str,
    revision_notes: str,
) -> dict[str, str]:
    return {
        "brand": brand,
        "strategy": strategy,
        "plan_item": plan_item,
        "idea": idea,
        "claims": claims,
        "source": source,
        "revision_notes": revision_notes,
    }


# ------------------------------------------------------------------ composition
# Threads are stored as one body with posts separated by a line containing only "---",
# so generated and human-edited versions go through the same code path.
POST_SEPARATOR = "\n\n---\n\n"
_SEPARATOR_LINE = re.compile(r"^\s*---\s*$", re.MULTILINE)
_WS = re.compile(r"\s+")
_TIMESTAMP_LINE = re.compile(r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}\s+\S", re.MULTILINE)
THREADED = frozenset({Platform.X, Platform.MASTODON})
VIDEO = frozenset({Platform.YOUTUBE, Platform.RUMBLE})
# Reddit and Substack do not use hashtags; any a human adds are not appended.
NO_HASHTAGS = frozenset({Platform.REDDIT, Platform.SUBSTACK})


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def _tag(tag: str) -> str:
    return "#" + re.sub(r"[^\w]", "", tag.lstrip("#"))


def normalize_hashtags(tags: list[str]) -> list[str]:
    return [t for t in (_tag(x) for x in tags) if len(t) > 1]


def _missing(snippet: str | None, text: str) -> bool:
    return bool(snippet and snippet.strip()) and _norm(snippet or "") not in _norm(text)


def _starts(body: str, hook: str) -> bool:
    return _norm(body).startswith(_norm(hook))


_HASHTAG = re.compile(r"(?<![\w#])#(\w+)")


def _present_tags(texts: list[str]) -> set[str]:
    return {t.casefold() for text in texts for t in _HASHTAG.findall(text)}


def split_posts(body: str) -> list[str]:
    return [p.strip() for p in _SEPARATOR_LINE.split(body) if p.strip()]


def fields_from_output(output: WriterOutput) -> VersionFields:
    return output.to_fields()


def _tail(texts: list[str], fields: VersionFields, hashtags: list[str]) -> list[str]:
    """The CTA and hashtags not already present in ``texts``, as blocks to append."""
    blocks: list[str] = []
    if _missing(fields.cta, "\n".join(texts)):
        blocks.append((fields.cta or "").strip())
    present = _present_tags([*texts, *blocks])
    extra = [t for t in hashtags if t[1:].casefold() not in present]
    if extra:
        blocks.append(" ".join(extra))
    return blocks


def _strings(value: object) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def _dicts(value: object) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def production_parts(platform: Platform, extras: Mapping[str, Any]) -> tuple[str, ...]:
    """Scenes/slides (Instagram) or script sections (video) as reviewable text."""
    if platform is Platform.INSTAGRAM:
        return tuple(
            " | ".join(p for p in (str(f.get("visual", "")), str(f.get("text", ""))) if p)
            for f in _dicts(extras.get("frames"))
        )
    if platform in VIDEO:
        return tuple(
            f"{s.get('heading', '')}\n{s.get('text', '')}".strip()
            for s in _dicts(extras.get("sections"))
        )
    return ()


def _notes(extras: Mapping[str, Any]) -> tuple[str, ...]:
    notes = [
        *_strings(extras.get("title_candidates")),
        *_strings(extras.get("thumbnail_concepts")),
    ]
    for key in ("pinned_comment", "visual_direction", "subtitle", "lead_magnet"):
        value = extras.get(key)
        if isinstance(value, str) and value.strip():
            notes.append(value.strip())
    return tuple(notes)


def _chapter_block(extras: Mapping[str, Any]) -> str:
    return "\n".join(
        f"{c.get('at', '')} {c.get('title', '')}".strip() for c in _dicts(extras.get("chapters"))
    )


def _text(extras: Mapping[str, Any], key: str) -> str | None:
    value = extras.get(key)
    return value if isinstance(value, str) and value.strip() else None


def build_draft(platform: Platform, fmt: ContentFormat, fields: VersionFields) -> Draft:
    """Compose exactly what would be published; QA checks this, not the raw fields."""
    hashtags = [] if platform in NO_HASHTAGS else normalize_hashtags(fields.hashtags)
    extras = fields.extras
    hook, body = fields.hook.strip(), fields.body.strip()
    if platform in THREADED:
        posts = split_posts(fields.body)
        if posts:
            posts[-1] = "\n\n".join([posts[-1], *_tail(posts, fields, hashtags)])
        segments = tuple(posts)
    else:
        if platform in VIDEO:
            # The description is published; the spoken hook and the script are parts.
            parts = [body]
            chapters = _chapter_block(extras)
            if chapters and not _TIMESTAMP_LINE.search(body):
                parts.append(chapters)
        elif platform is Platform.REDDIT or _starts(body, hook):
            # Reddit's hook is its title; writers sometimes repeat the hook as line one.
            parts = [body]
        else:
            parts = [hook, body]
        segments = ("\n\n".join(p for p in [*parts, *_tail(parts, fields, hashtags)] if p),)
    return Draft(
        platform=platform,
        format=fmt,
        hook=fields.hook,
        body=fields.body,
        cta=fields.cta,
        segments=segments,
        hashtags=tuple(hashtags),
        cited_claims=tuple(fields.cited_claims),
        title=fields.title,
        parts=production_parts(platform, extras),
        notes=_notes(extras),
        community=_text(extras, "subreddit"),
        flair=_text(extras, "flair"),
        content_warning=_text(extras, "content_warning"),
    )
