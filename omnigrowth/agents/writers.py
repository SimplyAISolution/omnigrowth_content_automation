"""Platform writer agents (§7E, §8): LinkedIn and X (A7 MVP scope).

Each writer has its own prompt and schema. ``fields_from_output`` maps model output to
``content_versions`` columns; ``build_draft`` composes the exact text that would be
published (the QA ``Draft``) from those columns, for generated and human-edited versions. Length
limits are deliberately NOT part of the schemas: an over-long post must reach QA and be
rejected there (§11 stage 4), not be silently repaired.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from omnigrowth.agents.base import AgentSpec
from omnigrowth.content.qa import Draft
from omnigrowth.core.enums import ContentFormat, ModelRole, Platform

# One local model for planning and writing keeps a 12 GB GPU from swapping models between
# calls (config/models.yaml); switch to FRONTIER_CREATIVE once a second model fits.
WRITER_ROLE = ModelRole.REASONING_LOCAL


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


class XPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posts: list[str] = Field(min_length=1, max_length=25)
    cta: str | None = Field(default=None, max_length=400)
    hashtags: list[str] = Field(default_factory=list, max_length=5)
    cited_claims: list[int] = Field(default_factory=list, max_length=10)
    hook_type: str | None = Field(default=None, max_length=40)
    cta_type: str | None = Field(default=None, max_length=40)


LINKEDIN = AgentSpec(
    name="linkedin_writer",
    prompt_ref="content/linkedin_writer@v1",
    schema=LinkedInPost,
    role=WRITER_ROLE,
)
X = AgentSpec(name="x_writer", prompt_ref="content/x_writer@v1", schema=XPost, role=WRITER_ROLE)
WRITERS: dict[Platform, AgentSpec[Any]] = {Platform.LINKEDIN: LINKEDIN, Platform.X: X}


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
# X threads are stored as one body with posts separated by a line containing only "---",
# so generated and human-edited versions go through the same code path.
POST_SEPARATOR = "\n\n---\n\n"
_SEPARATOR_LINE = re.compile(r"^\s*---\s*$", re.MULTILINE)
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def _tag(tag: str) -> str:
    return "#" + re.sub(r"[^\w]", "", tag.lstrip("#"))


def normalize_hashtags(tags: list[str]) -> list[str]:
    return [t for t in (_tag(x) for x in tags) if len(t) > 1]


def _missing(snippet: str | None, text: str) -> bool:
    return bool(snippet and snippet.strip()) and _norm(snippet or "") not in _norm(text)


_HASHTAG = re.compile(r"(?<![\w#])#(\w+)")


def _present_tags(texts: list[str]) -> set[str]:
    return {t.casefold() for text in texts for t in _HASHTAG.findall(text)}


def split_posts(body: str) -> list[str]:
    return [p.strip() for p in _SEPARATOR_LINE.split(body) if p.strip()]


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

    @property
    def payload(self) -> dict[str, Any]:
        return {"hashtags": self.hashtags, "cited_claims": self.cited_claims}


def fields_from_output(output: LinkedInPost | XPost) -> VersionFields:
    if isinstance(output, LinkedInPost):
        return VersionFields(
            hook=output.hook.strip(),
            body=output.body.strip(),
            cta=output.cta.strip() if output.cta else None,
            hashtags=normalize_hashtags(output.hashtags),
            cited_claims=list(output.cited_claims),
            hook_type=output.hook_type,
            cta_type=output.cta_type,
            media_requirements=[output.media_suggestion] if output.media_suggestion else [],
        )
    posts = [p.strip() for p in output.posts if p.strip()]
    return VersionFields(
        hook=posts[0].split("\n", 1)[0].strip() if posts else "",
        body=POST_SEPARATOR.join(posts),
        cta=output.cta.strip() if output.cta else None,
        hashtags=normalize_hashtags(output.hashtags),
        cited_claims=list(output.cited_claims),
        hook_type=output.hook_type,
        cta_type=output.cta_type,
    )


def build_draft(platform: Platform, fmt: ContentFormat, fields: VersionFields) -> Draft:
    """Compose exactly what would be published; QA checks this, not the raw fields."""
    hashtags = normalize_hashtags(fields.hashtags)
    if platform is Platform.X:
        posts = split_posts(fields.body)
        if posts:
            last = posts[-1]
            if _missing(fields.cta, "\n".join(posts)):
                last = f"{last}\n\n{(fields.cta or '').strip()}"
            present = _present_tags(posts)
            extra = [t for t in hashtags if t[1:].casefold() not in present]
            if extra:
                last = f"{last}\n\n{' '.join(extra)}"
            posts[-1] = last
        segments = tuple(posts)
    else:
        hook, body = fields.hook.strip(), fields.body.strip()
        # Writers sometimes repeat the hook as the body's first line; publish it once.
        parts = [body] if _norm(body).startswith(_norm(hook)) else [hook, body]
        if _missing(fields.cta, body):
            parts.append((fields.cta or "").strip())
        present = _present_tags(parts)
        extra = [t for t in hashtags if t[1:].casefold() not in present]
        if extra:
            parts.append(" ".join(extra))
        segments = ("\n\n".join(p for p in parts if p),)
    return Draft(
        platform=platform,
        format=fmt,
        hook=fields.hook,
        body=fields.body,
        cta=fields.cta,
        segments=segments,
        hashtags=tuple(hashtags),
        cited_claims=tuple(fields.cited_claims),
    )
