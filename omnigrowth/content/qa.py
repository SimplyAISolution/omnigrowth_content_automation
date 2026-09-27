"""Content QA stages 1-4 (spec §11) — deterministic checks, no model calls.

Each stage returns PASS / WARN / FAIL with machine-readable findings; any FAIL makes the
asset ``QA_FAILED`` (CA5). Stage 5 (human approval) lives in the approval flow.

* STRUCTURAL — hook/body present, CTA present when required, thread shape, minimum length.
* BRAND      — banned phrases (FAIL), avoid-list terms (WARN), duplicate/fatigue hits from
               pgvector (§10, WARN only — never deletes or blocks).
* ACCURACY   — links must be citations from the research packet (or the offer's own URL),
               cited claim numbers must exist, statistics must appear in the evidence,
               long quotations must come from the evidence (WARN).
* PLATFORM   — supported format, per-post / per-text length, hashtags, malformed URLs,
               markdown the platform will not render.

Limits come from ``config/qa.yaml`` (values UNVERIFIED until Phase 13).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field

from omnigrowth.content.platform_profiles import SubredditProfile, normalize_subreddit
from omnigrowth.core.enums import ClaimLabel, ContentFormat, Platform, QaOutcome, QaStage
from omnigrowth.core.errors import ErrorCode, OmniGrowthError

CHECKER = "omnigrowth.content.qa@1"


# ------------------------------------------------------------------ configuration
class PartRange(BaseModel):
    """How many production parts (scenes, slides, script sections) a format needs."""

    model_config = ConfigDict(extra="forbid")

    min: int = Field(ge=0)
    max: int = Field(ge=1)


class PlatformRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formats: list[ContentFormat]
    max_chars: int | None = None
    max_chars_per_post: int | None = None
    min_chars: int = 0
    hook_visible_chars: int | None = None
    max_thread_posts: int | None = None
    max_hashtags: int = 5
    markdown_rendered: bool = False
    # Platforms that shorten links count every URL as this many characters (X: t.co).
    url_chars: int | None = None
    # Multi-post platforms (X, Mastodon): a THREAD needs 2+ posts, a TEXT_POST exactly 1.
    threaded: bool = False
    max_title_chars: int | None = None
    title_required: list[ContentFormat] = Field(default_factory=list)
    # Production parts per format; formats not listed accept any number (including none).
    parts: dict[ContentFormat, PartRange] = Field(default_factory=dict)
    # Description chapters (YouTube): when timestamp lines are present they must start at
    # 0:00, ascend, and number at least this many, or the platform ignores them.
    chapters_min: int | None = None
    # Per-format limit on the published text, where it differs from max_chars.
    format_max_chars: dict[ContentFormat, int] = Field(default_factory=dict)


class ModelThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    near_duplicate: float = Field(ge=0.0, le=1.0)
    repeated_hook: float = Field(ge=0.0, le=1.0)


class SimilarityRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    near_duplicate: float = Field(ge=0.0, le=1.0)
    repeated_hook: float = Field(ge=0.0, le=1.0)
    max_neighbours: int = Field(ge=1, le=50)
    per_model: dict[str, ModelThresholds] = Field(default_factory=dict)

    def for_model(self, model: str | None) -> ModelThresholds:
        """Thresholds calibrated for this embedding model, else the defaults."""
        override = self.per_model.get(model or "")
        return override or ModelThresholds(
            near_duplicate=self.near_duplicate, repeated_hook=self.repeated_hook
        )


class QaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    similarity: SimilarityRules
    platforms: dict[Platform, PlatformRules]


def load_qa_config(config_dir: Path) -> QaConfig:
    path = config_dir / "qa.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return QaConfig.model_validate(raw)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise OmniGrowthError(
            ErrorCode.CONFIGURATION_ERROR,
            "invalid config/qa.yaml",
            details={"path": str(path), "error": type(exc).__name__},
        ) from exc


@lru_cache(maxsize=4)
def cached_qa_config(config_dir: Path) -> QaConfig:
    return load_qa_config(config_dir)


# ------------------------------------------------------------------ inputs / outputs
@dataclass(frozen=True, slots=True)
class Draft:
    """What gets published: ``segments`` are the composed texts (one per X/Mastodon post).

    ``title`` is published separately (YouTube, Reddit, Substack, Rumble). ``parts`` are
    production material the human turns into media (scenes, slides, script sections) and
    ``notes`` other generated text (title candidates, thumbnail concepts, pinned comment):
    neither is length-checked as published text, but both go through the brand and
    accuracy stages because they reach an audience too.
    """

    platform: Platform
    format: ContentFormat
    hook: str
    body: str
    cta: str | None
    segments: tuple[str, ...]
    hashtags: tuple[str, ...] = ()
    cited_claims: tuple[int, ...] = ()
    title: str | None = None
    parts: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    community: str | None = None  # REDDIT: target subreddit
    flair: str | None = None
    # MASTODON: shown before each post and counted in the instance's post limit.
    content_warning: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimRef:
    label: ClaimLabel
    text: str
    citation_url: str | None = None


@dataclass(frozen=True, slots=True)
class SimilarHit:
    """A pgvector neighbour of this version (another asset of the same brand)."""

    field: str  # "FULL" | "HOOK"
    content_asset_id: str
    content_version_id: str
    similarity: float


@dataclass(frozen=True, slots=True)
class QaContext:
    cta_required: bool
    expected_cta: str | None = None  # the plan item's CTA; accepted inline in the text
    banned_phrases: tuple[str, ...] = ()
    avoid_terms: tuple[str, ...] = ()
    claims: dict[int, ClaimRef] = field(default_factory=dict)
    evidence_text: str = ""
    allowed_urls: frozenset[str] = frozenset()
    similar: tuple[SimilarHit, ...] = ()
    # False when no embedding model was reachable: reported, never blocking (§10).
    similarity_checked: bool = True
    embedding_model: str | None = None  # selects the calibrated thresholds
    # REDDIT: the brand's configured subreddit profiles (platform_accounts.settings).
    subreddits: tuple[SubredditProfile, ...] = ()
    # MASTODON: the strictest configured instance limit, overriding config/qa.yaml.
    max_chars_per_post: int | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: QaOutcome  # WARN | FAIL
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: QaStage
    outcome: QaOutcome
    score: int
    findings: tuple[Finding, ...]


def _result(stage: QaStage, findings: Iterable[Finding]) -> StageResult:
    found = tuple(findings)
    fails = sum(f.severity is QaOutcome.FAIL for f in found)
    warns = sum(f.severity is QaOutcome.WARN for f in found)
    outcome = QaOutcome.FAIL if fails else QaOutcome.WARN if warns else QaOutcome.PASS
    return StageResult(stage, outcome, max(0, 100 - 40 * fails - 10 * warns), found)


def _fail(code: str, message: str, **detail: Any) -> Finding:
    return Finding(code, QaOutcome.FAIL, message, detail)


def _warn(code: str, message: str, **detail: Any) -> Finding:
    return Finding(code, QaOutcome.WARN, message, detail)


# ------------------------------------------------------------------ helpers
# Explicit links, plus bare domains on common TLDs (LinkedIn and X auto-link those too).
_URL = re.compile(
    r"(?:https?://|www\.)[^\s<>()\[\]{}\"']+"
    r"|(?<![@\w.\-/])(?:[a-z0-9-]+\.)+"
    r"(?:com|org|net|io|ai|co|dev|app|info|biz|me|ly|us|uk|de|fr|eu|example)"
    r"(?![\w-])(?:/[^\s<>()\[\]{}\"']*)?",
    re.IGNORECASE,
)
_HASHTAG = re.compile(r"(?<![\w#])#(\w*[^\W\d_]\w*)")
_MD_BOLD = re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s", re.MULTILINE)
_STATISTIC = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:[kmb]|bn|million|billion)\b)?"
    r"|(?<![\d.,])\d+(?:[.,]\d+)?\s?%"
    r"|(?<![\d.,])\d+(?:[.,]\d+)?\s?x\b"
    r"|(?<![\d.,])\d+(?:[.,]\d+)?\s?(?:percent|million|billion)\b",
    re.IGNORECASE,
)
_QUOTE = re.compile(r"[\"“]([^\"”]{30,400})[\"”]")
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def _compact(text: str) -> str:
    return re.sub(r"[\s,]", "", text).casefold()


def _full_text(draft: Draft) -> str:
    return "\n\n".join(draft.segments)


def _checked_text(draft: Draft) -> str:
    """Everything that reaches an audience: title, published text, parts and notes."""
    extra = [*draft.parts, *draft.notes, draft.content_warning or ""]
    return "\n".join([draft.title or "", draft.hook, _full_text(draft), draft.cta or "", *extra])


_CHAPTER = re.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s+\S", re.MULTILINE)


def _seconds(stamp: str) -> int:
    total = 0
    for piece in stamp.split(":"):
        total = total * 60 + int(piece)
    return total


def _clean_url(url: str) -> str:
    """Comparison key: scheme, ``www.``, query/fragment and trailing slash ignored; host
    case-folded, path case kept (paths are case-sensitive)."""
    raw = url.rstrip(".,;:!?")
    parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = (parts.hostname or "").casefold().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}"


def _statistics(text: str) -> set[str]:
    """Whole figures (``35%``, ``$500``, ``12x``) as comparison keys; ``5%`` is not ``35%``."""
    return {_compact(m.group(0)) for m in _STATISTIC.finditer(text)}


def _phrase_hits(text: str, phrases: Iterable[str]) -> list[str]:
    hits: list[str] = []
    for phrase in phrases:
        phrase = phrase.strip()
        if not phrase:
            continue
        left = r"\b" if phrase[0].isalnum() else ""
        right = r"\b" if phrase[-1].isalnum() else ""
        if re.search(left + re.escape(phrase) + right, text, re.IGNORECASE):
            hits.append(phrase)
    return hits


def _hashtags(draft: Draft) -> list[str]:
    """Hashtags as published: ``segments`` already contain the composed tag line."""
    return [tag for segment in draft.segments for tag in _HASHTAG.findall(segment)]


def _counted_length(text: str, rules: PlatformRules) -> int:
    """Length as the platform counts it (shortened links count as ``url_chars``)."""
    if rules.url_chars is None:
        return len(text)
    urls = [u for u in _URL.findall(text) if "://" in u or u.lower().startswith("www.")]
    return len(text) - sum(len(u) for u in urls) + rules.url_chars * len(urls)


# ------------------------------------------------------------------ stages
def structural(draft: Draft, ctx: QaContext, rules: PlatformRules) -> StageResult:
    findings: list[Finding] = []
    if not draft.hook.strip():
        findings.append(_fail("MISSING_HOOK", "the post has no opening hook"))
    if not draft.body.strip() or not any(s.strip() for s in draft.segments):
        findings.append(_fail("EMPTY_BODY", "the post has no body"))
    written = bool(ctx.expected_cta) and _norm(ctx.expected_cta or "") in _norm(_full_text(draft))
    # Reddit posts are community-native (spec §8): a call to action is never required.
    required = ctx.cta_required and draft.platform is not Platform.REDDIT
    if required and not (draft.cta and draft.cta.strip()) and not written:
        findings.append(_fail("MISSING_CTA", "this plan item requires a call to action"))
    if draft.format in rules.title_required and not (draft.title and draft.title.strip()):
        findings.append(_fail("MISSING_TITLE", f"{draft.format.value} needs a title"))
    span = rules.parts.get(draft.format)
    if span is not None and not span.min <= len(draft.parts) <= span.max:
        code = "TOO_FEW_PARTS" if len(draft.parts) < span.min else "TOO_MANY_PARTS"
        findings.append(
            _fail(
                code,
                f"{draft.format.value} needs {span.min}-{span.max} parts",
                parts=len(draft.parts),
                min=span.min,
                max=span.max,
            )
        )
    if rules.threaded:
        posts = len(draft.segments)
        if draft.format is ContentFormat.THREAD and posts < 2:
            findings.append(_fail("THREAD_TOO_SHORT", "a thread needs at least 2 posts"))
        if draft.format is ContentFormat.TEXT_POST and posts != 1:
            findings.append(
                _fail("NOT_A_SINGLE_POST", "a standalone post must be one post", posts=posts)
            )
        if rules.max_thread_posts and posts > rules.max_thread_posts:
            findings.append(
                _fail("THREAD_TOO_LONG", "too many posts", posts=posts, max=rules.max_thread_posts)
            )
    if len(_full_text(draft)) < rules.min_chars:
        findings.append(
            _warn("TOO_SHORT", "shorter than typical for this platform", min=rules.min_chars)
        )
    return _result(QaStage.STRUCTURAL, findings)


def brand(draft: Draft, ctx: QaContext, config: QaConfig) -> StageResult:
    text = _checked_text(draft)
    findings = [
        _fail("BANNED_PHRASE", f"uses banned phrase {p!r}", phrase=p)
        for p in _phrase_hits(text, ctx.banned_phrases)
    ]
    findings += [
        _warn("AVOID_TERM", f"uses avoid-list term {p!r}", term=p)
        for p in _phrase_hits(text, ctx.avoid_terms)
    ]
    if not ctx.similarity_checked:
        findings.append(
            _warn("DUPLICATE_CHECK_SKIPPED", "no embedding model available; not checked")
        )
    rules = config.similarity.for_model(ctx.embedding_model)
    for hit in ctx.similar:
        if hit.field == "FULL" and hit.similarity >= rules.near_duplicate:
            findings.append(
                _warn(
                    "NEAR_DUPLICATE",
                    "very similar to existing content (fatigue risk)",
                    content_asset_id=hit.content_asset_id,
                    content_version_id=hit.content_version_id,
                    similarity=round(hit.similarity, 4),
                    threshold=rules.near_duplicate,
                )
            )
        elif hit.field == "HOOK" and hit.similarity >= rules.repeated_hook:
            findings.append(
                _warn(
                    "REPEATED_HOOK",
                    "the hook repeats an existing hook",
                    content_asset_id=hit.content_asset_id,
                    content_version_id=hit.content_version_id,
                    similarity=round(hit.similarity, 4),
                    threshold=rules.repeated_hook,
                )
            )
    return _result(QaStage.BRAND, findings)


def accuracy(draft: Draft, ctx: QaContext) -> StageResult:
    findings: list[Finding] = []
    text = _checked_text(draft)
    allowed = {_clean_url(u) for u in ctx.allowed_urls}
    for url in dict.fromkeys(_URL.findall(text)):
        if _clean_url(url) not in allowed:
            findings.append(
                _fail(
                    "CITATION_NOT_IN_PACKET",
                    "links to a source that is not in the research packet",
                    url=url,
                )
            )
    for number in draft.cited_claims:
        claim = ctx.claims.get(number)
        if claim is None:
            findings.append(
                _fail("UNKNOWN_CLAIM", "cites a claim missing from the packet", claim=number)
            )
        elif claim.label is not ClaimLabel.SOURCE_STATED:
            findings.append(
                _warn(
                    "CITES_UNVERIFIED_CLAIM",
                    f"relies on a claim labelled {claim.label.value}",
                    claim=number,
                )
            )
    evidence = _statistics(ctx.evidence_text)
    for stat in dict.fromkeys(m.group(0) for m in _STATISTIC.finditer(text)):
        if _compact(stat) not in evidence:
            findings.append(
                _fail(
                    "UNSUPPORTED_STATISTIC",
                    "states a figure that does not appear in the source or research",
                    figure=stat.strip(),
                )
            )
    evidence_norm = _norm(ctx.evidence_text)
    for quote in _QUOTE.findall(text):
        if _norm(quote) not in evidence_norm:
            findings.append(
                _warn("UNSOURCED_QUOTE", "quotation not found in the source", quote=quote[:120])
            )
    return _result(QaStage.ACCURACY, findings)


def platform(draft: Draft, rules: PlatformRules, ctx: QaContext | None = None) -> StageResult:
    findings: list[Finding] = []
    title = (draft.title or "").strip()
    if rules.max_title_chars is not None and len(title) > rules.max_title_chars:
        findings.append(
            _fail(
                "TITLE_TOO_LONG",
                f"title is {len(title)} characters",
                length=len(title),
                max=rules.max_title_chars,
            )
        )
    if rules.chapters_min is not None:
        findings += _chapter_findings(draft, rules.chapters_min)
    if draft.platform is Platform.REDDIT and ctx is not None:
        findings += _community_findings(draft, ctx)
    if draft.format not in rules.formats:
        findings.append(
            _fail(
                "UNSUPPORTED_FORMAT",
                f"{draft.format.value} is not supported on {draft.platform.value}",
            )
        )
    if rules.max_chars_per_post is not None:
        warning = len(draft.content_warning or "")
        for index, segment in enumerate(draft.segments, start=1):
            length = _counted_length(segment, rules) + warning
            if length > rules.max_chars_per_post:
                findings.append(
                    _fail(
                        "OVER_LENGTH",
                        f"post {index} is {length} characters",
                        post=index,
                        length=length,
                        max=rules.max_chars_per_post,
                    )
                )
    if rules.max_chars is not None and len(_full_text(draft)) > rules.max_chars:
        findings.append(
            _fail(
                "OVER_LENGTH",
                f"text is {len(_full_text(draft))} characters",
                length=len(_full_text(draft)),
                max=rules.max_chars,
            )
        )
    format_max = rules.format_max_chars.get(draft.format)
    if format_max is not None and len(_full_text(draft)) > format_max:
        findings.append(
            _fail(
                "OVER_LENGTH",
                f"a {draft.format.value} is at most {format_max} characters",
                length=len(_full_text(draft)),
                max=format_max,
            )
        )
    if rules.hook_visible_chars is not None and len(draft.hook) > rules.hook_visible_chars:
        findings.append(
            _warn(
                "HOOK_TRUNCATED",
                "the hook is cut off before 'see more'",
                length=len(draft.hook),
                visible=rules.hook_visible_chars,
            )
        )
    tags = _hashtags(draft)
    lowered = [t.casefold() for t in tags]
    duplicates = sorted({t for t in lowered if lowered.count(t) > 1})
    if duplicates:
        findings.append(_warn("DUPLICATE_HASHTAG", "repeated hashtags", hashtags=duplicates))
    if len(set(lowered)) > rules.max_hashtags:
        findings.append(_warn("TOO_MANY_HASHTAGS", "too many hashtags", count=len(set(lowered))))
    for url in dict.fromkeys(_URL.findall(_full_text(draft))):
        parts = urlsplit(url if "://" in url else f"https://{url}")
        host = parts.hostname or ""
        if "." not in host or host.startswith(".") or host.endswith("."):
            findings.append(_fail("BROKEN_URL", "malformed URL", url=url))
    if not rules.markdown_rendered and any(
        _MD_BOLD.search(s) or _MD_HEADING.search(s) for s in draft.segments
    ):
        findings.append(
            _warn("MARKDOWN_NOT_RENDERED", "markdown formatting will show up literally")
        )
    return _result(QaStage.PLATFORM, findings)


def _chapter_findings(draft: Draft, minimum: int) -> list[Finding]:
    stamps = [_seconds(m) for m in _CHAPTER.findall(_full_text(draft))]
    if not stamps:
        return []
    problems: list[str] = []
    if stamps[0] != 0:
        problems.append("the first chapter must start at 0:00")
    if any(b <= a for a, b in pairwise(stamps)):
        problems.append("chapter timestamps must ascend")
    if len(stamps) < minimum:
        problems.append(f"at least {minimum} chapters are needed")
    return (
        [_warn("CHAPTERS_IGNORED", "chapters will not be shown: " + "; ".join(problems))]
        if problems
        else []
    )


def _community_findings(draft: Draft, ctx: QaContext) -> list[Finding]:
    """Per-subreddit rules (spec §8). Without profiles the post can still be drafted."""
    if not ctx.subreddits:
        return [_warn("NO_SUBREDDIT_PROFILE", "no subreddit is configured for this brand")]
    name = normalize_subreddit(draft.community or "").casefold()
    profile = next((p for p in ctx.subreddits if p.subreddit.casefold() == name), None)
    if profile is None:
        return [
            _fail(
                "UNKNOWN_SUBREDDIT",
                "the target subreddit is not one of the configured profiles",
                subreddit=draft.community,
                configured=[p.subreddit for p in ctx.subreddits],
            )
        ]
    findings: list[Finding] = []
    text = _checked_text(draft)
    findings += [
        _fail("SUBREDDIT_BANNED_PHRASE", f"r/{profile.subreddit} bans {p!r}", phrase=p)
        for p in _phrase_hits(text, profile.banned_phrases)
    ]
    if not profile.text_posts_allowed:
        findings.append(_fail("POST_TYPE_NOT_ALLOWED", "text posts are not allowed here"))
    promoted = bool(draft.cta and draft.cta.strip()) or bool(
        ctx.expected_cta and _norm(ctx.expected_cta) in _norm(_full_text(draft))
    )
    if profile.self_promotion_policy == "BANNED" and promoted:
        findings.append(
            _fail("SELF_PROMOTION_BANNED", "this subreddit bans self-promotion; drop the CTA")
        )
    title = (draft.title or "").strip()
    if profile.max_title_chars is not None and len(title) > profile.max_title_chars:
        findings.append(
            _fail(
                "TITLE_TOO_LONG", "title exceeds the subreddit limit", max=profile.max_title_chars
            )
        )
    if profile.title_prefix and not title.startswith(profile.title_prefix):
        findings.append(
            _fail(
                "TITLE_PREFIX_MISSING", "missing required title prefix", prefix=profile.title_prefix
            )
        )
    flair = (draft.flair or "").strip()
    allowed = {f.casefold() for f in profile.allowed_flairs}
    if profile.flair_required and not flair:
        findings.append(_fail("FLAIR_REQUIRED", "this subreddit requires post flair"))
    elif flair and allowed and flair.casefold() not in allowed:
        findings.append(_fail("UNKNOWN_FLAIR", "flair is not in the allowed list", flair=flair))
    if profile.disclosure_required:
        findings.append(
            _warn("DISCLOSURE_REQUIRED", "the subreddit requires disclosing affiliation; check it")
        )
    if profile.stale():
        findings.append(
            _warn(
                "SUBREDDIT_PROFILE_STALE",
                "subreddit rules were not reviewed in the last 90 days",
                last_reviewed_at=profile.last_reviewed_at.isoformat()
                if profile.last_reviewed_at
                else None,
            )
        )
    return findings


def run_qa(draft: Draft, ctx: QaContext, config: QaConfig) -> list[StageResult]:
    rules = config.platforms.get(draft.platform)
    if rules is None:
        raise OmniGrowthError(
            ErrorCode.CONFIGURATION_ERROR,
            f"no QA rules for platform {draft.platform.value}",
            details={"platform": draft.platform.value},
        )
    if ctx.max_chars_per_post is not None and rules.max_chars_per_post is not None:
        rules = rules.model_copy(update={"max_chars_per_post": ctx.max_chars_per_post})
    return [
        structural(draft, ctx, rules),
        brand(draft, ctx, config),
        accuracy(draft, ctx),
        platform(draft, rules, ctx),
    ]


def overall_score(results: list[StageResult]) -> int:
    return round(sum(r.score for r in results) / len(results)) if results else 0
