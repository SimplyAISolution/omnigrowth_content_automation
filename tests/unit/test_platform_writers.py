"""Phase 10 gate: every platform writer has format tests.

For each writer: its prompt is bound to its schema, each supported format composes into a
draft that passes QA, and the format rules (titles, scenes/slides/sections, chapters,
threads, per-subreddit and per-instance settings) fail the way they should.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from omnigrowth.agents import writers
from omnigrowth.agents.writers import (
    WRITERS,
    FacebookPost,
    InstagramPost,
    MastodonPost,
    RedditPost,
    RumbleVideo,
    SubstackPost,
    VersionFields,
    YouTubeVideo,
    build_draft,
    fields_from_output,
    merge_extras,
)
from omnigrowth.content.platform_profiles import (
    SubredditProfile,
    mastodon_max_characters,
    subreddit_profiles,
    validate_settings,
)
from omnigrowth.content.qa import ClaimRef, QaContext, StageResult, load_qa_config, run_qa
from omnigrowth.core.enums import ClaimLabel, ContentFormat, Platform, QaOutcome, QaStage
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.prompts.registry import PromptRegistry

REPO = Path(__file__).resolve().parents[2]
CONFIG = load_qa_config(REPO / "config")
EVIDENCE = (
    "Local AI agents keep proprietary data on the machine. A 9B model runs on a GPU with "
    "12 GB of VRAM. Routine work avoids per-token cloud fees."
)
CTA = "Subscribe for the local-AI checklist"
SENTENCE = "Local AI agents keep proprietary data on the machine, so client files stay put."


def ctx(**overrides: object) -> QaContext:
    base: dict[str, object] = {
        "cta_required": True,
        "banned_phrases": ("game-changer",),
        "claims": {1: ClaimRef(ClaimLabel.SOURCE_STATED, "Data stays on the machine.")},
        "evidence_text": EVIDENCE,
    }
    base.update(overrides)
    return QaContext(**base)  # type: ignore[arg-type]


def qa(
    platform: Platform, fmt: ContentFormat, fields: VersionFields, **overrides: object
) -> dict[QaStage, StageResult]:
    draft = build_draft(platform, fmt, fields)
    return {r.stage: r for r in run_qa(draft, ctx(**overrides), CONFIG)}


def codes(results: dict[QaStage, StageResult]) -> set[str]:
    return {f.code for r in results.values() for f in r.findings}


def fails(results: dict[QaStage, StageResult]) -> set[str]:
    return {f.code for r in results.values() for f in r.findings if f.severity is QaOutcome.FAIL}


# ------------------------------------------------------------------ sample outputs
def youtube(**overrides: Any) -> YouTubeVideo:
    data: dict[str, Any] = {
        "title_candidates": [
            "Run AI Without Sending Client Data Anywhere",
            "Local AI for Small Firms, Explained",
            "Your Data Stays Home: Local AI Agents",
        ],
        "title": "Run AI Without Sending Client Data Anywhere",
        "hook": "What if your AI never saw the cloud at all?",
        "sections": [
            {"heading": "The problem", "text": "Client files and cloud AI do not mix."},
            {"heading": "The setup", "text": "A 9B model runs on a GPU with 12 GB of VRAM."},
            {"heading": "What to run", "text": "Start with routine drafting work."},
            {"heading": "Wrap-up", "text": "Keep hard reasoning for frontier models."},
        ],
        "description": f"{SENTENCE}\n\nThis video walks through a practical local setup.",
        "chapters": [
            {"at": "0:00", "title": "Intro"},
            {"at": "1:10", "title": "The setup"},
            {"at": "4:30", "title": "What to run"},
        ],
        "thumbnail_concepts": ["Laptop inside a padlock; overlay: DATA STAYS HOME"],
        "pinned_comment": "Which task would you move to a local model first?",
        "cta": CTA,
        "hashtags": ["LocalAI"],
        "cited_claims": [1],
        "hook_type": "question",
        "cta_type": "subscribe",
    }
    data.update(overrides)
    return YouTubeVideo.model_validate(data)


def rumble(**overrides: Any) -> RumbleVideo:
    data = youtube().model_dump(exclude={"chapters", "pinned_comment"})
    data.update(overrides)
    return RumbleVideo.model_validate(data)


def instagram(frames: int = 5, **overrides: Any) -> InstagramPost:
    data: dict[str, Any] = {
        "hook": "Your client data can stay in the office.",
        "caption": f"{SENTENCE}\n\nSave this for your next tooling review.",
        "cta": CTA,
        "hashtags": ["LocalAI", "SmallBusiness", "Privacy"],
        "cited_claims": [1],
        "visual_direction": "Clean desk shots, warm light, brand teal accents.",
        "frames": [
            {"visual": f"Slide {n} background", "text": f"Point {n}"} for n in range(frames)
        ],
    }
    data.update(overrides)
    return InstagramPost.model_validate(data)


def facebook(**overrides: Any) -> FacebookPost:
    data: dict[str, Any] = {
        "hook": "Would you let an AI read your client files?",
        "body": f"{SENTENCE}\n\nWe tried it for a month. What would you move first?",
        "cta": CTA,
        "hashtags": ["LocalAI"],
        "cited_claims": [1],
    }
    data.update(overrides)
    return FacebookPost.model_validate(data)


def reddit(**overrides: Any) -> RedditPost:
    data: dict[str, Any] = {
        "subreddit": "r/LocalLLaMA",
        "title": "What we learned running a 9B model for client drafting",
        "body": f"{SENTENCE}\n\n- drafting\n- ticket summaries\n\nWhat do you run locally?",
        "flair": "Discussion",
        "cta": None,
        "cited_claims": [1],
    }
    data.update(overrides)
    return RedditPost.model_validate(data)


def substack(**overrides: Any) -> SubstackPost:
    data: dict[str, Any] = {
        "title": "Why your AI should live in your office",
        "subtitle": "A practical case for local models",
        "hook": "Most small teams send client files to a cloud AI without thinking twice.",
        "body": f"## The case\n\n{SENTENCE}\n\n## What to run\n\nRoutine drafting first.",
        "cta": CTA,
        "lead_magnet": "A one-page local-AI readiness checklist.",
        "cited_claims": [1],
    }
    data.update(overrides)
    return SubstackPost.model_validate(data)


def mastodon(posts: list[str] | None = None, **overrides: Any) -> MastodonPost:
    data: dict[str, Any] = {
        "posts": posts or [SENTENCE, "Routine drafting is the easy first step."],
        "cta": CTA,
        "hashtags": ["LocalAI"],
        "content_warning": None,
        "cited_claims": [1],
    }
    data.update(overrides)
    return MastodonPost.model_validate(data)


SAMPLES: list[tuple[Platform, ContentFormat, BaseModel]] = [
    (Platform.YOUTUBE, ContentFormat.LONG_VIDEO_SCRIPT, youtube()),
    (Platform.YOUTUBE, ContentFormat.LONG_VIDEO_OUTLINE, youtube()),
    (
        Platform.YOUTUBE,
        ContentFormat.SHORT,
        youtube(sections=[{"heading": "Beat 1", "text": "Data stays put."}], chapters=[]),
    ),
    (Platform.INSTAGRAM, ContentFormat.REEL_SCRIPT, instagram(frames=5)),
    (Platform.INSTAGRAM, ContentFormat.CAROUSEL, instagram(frames=6)),
    (Platform.INSTAGRAM, ContentFormat.STORY, instagram(frames=3)),
    (Platform.INSTAGRAM, ContentFormat.IMAGE_POST, instagram(frames=1)),
    (Platform.FACEBOOK, ContentFormat.TEXT_POST, facebook()),
    (Platform.FACEBOOK, ContentFormat.IMAGE_POST, facebook(media_suggestion="Desk photo")),
    (Platform.REDDIT, ContentFormat.DISCUSSION, reddit()),
    (Platform.REDDIT, ContentFormat.TEXT_POST, reddit()),
    (Platform.SUBSTACK, ContentFormat.NEWSLETTER, substack()),
    (Platform.SUBSTACK, ContentFormat.ARTICLE, substack()),
    (Platform.SUBSTACK, ContentFormat.NOTE, substack(title=None, subtitle=None)),
    (Platform.RUMBLE, ContentFormat.LONG_VIDEO_SCRIPT, rumble()),
    (Platform.RUMBLE, ContentFormat.LONG_VIDEO_OUTLINE, rumble()),
    (
        Platform.RUMBLE,
        ContentFormat.SHORT,
        rumble(sections=[{"heading": "Beat", "text": "Data stays put."}]),
    ),
    (Platform.MASTODON, ContentFormat.THREAD, mastodon()),
    (Platform.MASTODON, ContentFormat.TEXT_POST, mastodon(posts=[SENTENCE])),
]

FRESH = SubredditProfile(
    subreddit="LocalLLaMA",
    allowed_flairs=["Discussion", "Tutorial"],
    last_reviewed_at=datetime.now(UTC),
)


# ------------------------------------------------------------------ bindings
@pytest.mark.parametrize("platform", list(Platform))
def test_every_platform_has_a_writer_bound_to_its_prompt_and_qa_rules(
    platform: Platform,
) -> None:
    spec = WRITERS[platform]
    template = PromptRegistry.load().get(spec.prompt_ref)
    module, _, name = template.front_matter.output_schema.rpartition(".")
    assert module == "omnigrowth.agents.writers"
    assert getattr(writers, name) is spec.schema
    keys = writers.variables(
        brand="", strategy="", plan_item="", idea="", claims="", source="", revision_notes=""
    )
    assert set(template.front_matter.variables) == set(keys)
    assert spec.role is writers.WRITER_ROLE
    assert platform in CONFIG.platforms


def test_samples_cover_every_configured_format_of_the_new_platforms() -> None:
    covered = {(p, f) for p, f, _ in SAMPLES}
    for platform in writers.EXTRAS.keys() | {Platform.FACEBOOK}:
        for fmt in CONFIG.platforms[platform].formats:
            assert (platform, fmt) in covered, (platform, fmt)


@pytest.mark.parametrize(
    ("platform", "fmt", "output"), SAMPLES, ids=[f"{p.value}-{f.value}" for p, f, _ in SAMPLES]
)
def test_each_supported_format_composes_and_passes_qa(
    platform: Platform, fmt: ContentFormat, output: BaseModel
) -> None:
    fields = fields_from_output(output)  # type: ignore[arg-type]
    results = qa(platform, fmt, fields, subreddits=(FRESH,))
    assert not fails(results), (platform, fmt, fails(results))
    # The stored payload round-trips: rebuilding from columns gives the same draft.
    rebuilt = VersionFields(
        hook=fields.hook,
        body=fields.body,
        cta=fields.cta,
        hashtags=list(fields.payload["hashtags"]),
        cited_claims=list(fields.payload["cited_claims"]),
        title=fields.title,
        extras=writers.extras_from_payload(fields.payload),
    )
    assert build_draft(platform, fmt, rebuilt) == build_draft(platform, fmt, fields)


def test_unsupported_formats_are_rejected_per_platform() -> None:
    assert "UNSUPPORTED_FORMAT" in fails(
        qa(Platform.INSTAGRAM, ContentFormat.TEXT_POST, fields_from_output(instagram()))
    )
    assert "UNSUPPORTED_FORMAT" in fails(
        qa(Platform.FACEBOOK, ContentFormat.THREAD, fields_from_output(facebook()))
    )
    assert "UNSUPPORTED_FORMAT" in fails(
        qa(Platform.YOUTUBE, ContentFormat.CAROUSEL, fields_from_output(youtube()))
    )


# ------------------------------------------------------------------ YouTube
def test_youtube_keeps_title_strategy_apart_and_publishes_the_description() -> None:
    fields = fields_from_output(youtube())
    draft = build_draft(Platform.YOUTUBE, ContentFormat.LONG_VIDEO_SCRIPT, fields)
    assert draft.title == "Run AI Without Sending Client Data Anywhere"
    assert fields.payload["title_candidates"][1] == "Local AI for Small Firms, Explained"
    (description,) = draft.segments
    assert description.startswith(SENTENCE)
    assert "0:00 Intro\n1:10 The setup\n4:30 What to run" in description
    assert description.rstrip().endswith("#LocalAI")
    assert "never saw the cloud" not in description  # the spoken hook is not published
    assert len(draft.parts) == 4 and draft.parts[1].startswith("The setup\n")
    assert "Local AI for Small Firms, Explained" in draft.notes
    assert fields.media_requirements == [
        "video (produced by a human from the script)",
        "thumbnail",
    ]


def test_youtube_title_rules() -> None:
    long_title = "A" * 101
    fields = fields_from_output(youtube(title=long_title))
    assert "TITLE_TOO_LONG" in fails(qa(Platform.YOUTUBE, ContentFormat.SHORT, fields))
    untitled = VersionFields(
        hook=fields.hook, body=fields.body, cta=CTA, hashtags=[], title=None, extras=fields.extras
    )
    assert "MISSING_TITLE" in fails(qa(Platform.YOUTUBE, ContentFormat.SHORT, untitled))
    with pytest.raises(ValidationError):
        youtube(title_candidates=["Only one"])


def test_youtube_script_needs_enough_sections() -> None:
    two = [{"heading": "A", "text": "Data stays put."}, {"heading": "B", "text": "Done."}]
    fields = fields_from_output(youtube(sections=two))
    assert "TOO_FEW_PARTS" in fails(qa(Platform.YOUTUBE, ContentFormat.LONG_VIDEO_SCRIPT, fields))
    short = fields_from_output(youtube(sections=two * 5))
    assert "TOO_MANY_PARTS" in fails(qa(Platform.YOUTUBE, ContentFormat.SHORT, short))


def test_youtube_chapters_that_youtube_would_ignore_warn() -> None:
    late = [
        {"at": "0:30", "title": "Intro"},
        {"at": "1:10", "title": "B"},
        {"at": "2:10", "title": "C"},
    ]
    results = qa(
        Platform.YOUTUBE,
        ContentFormat.LONG_VIDEO_SCRIPT,
        fields_from_output(youtube(chapters=late)),
    )
    assert "CHAPTERS_IGNORED" in codes(results)
    assert "CHAPTERS_IGNORED" not in fails(results)  # warning only
    two = [{"at": "0:00", "title": "A"}, {"at": "1:00", "title": "B"}]
    assert "CHAPTERS_IGNORED" in codes(
        qa(
            Platform.YOUTUBE,
            ContentFormat.LONG_VIDEO_SCRIPT,
            fields_from_output(youtube(chapters=two)),
        )
    )
    with pytest.raises(ValidationError):
        youtube(chapters=[{"at": "one minute", "title": "A"}])


def test_script_sections_and_notes_are_fact_checked() -> None:
    sections = [
        {"heading": "Claim", "text": "Local AI cuts costs by 73%."},
        {"heading": "B", "text": "Data stays put."},
        {"heading": "C", "text": "Done."},
    ]
    fields = fields_from_output(youtube(sections=sections))
    assert "UNSUPPORTED_STATISTIC" in fails(
        qa(Platform.YOUTUBE, ContentFormat.LONG_VIDEO_SCRIPT, fields)
    )
    thumb = fields_from_output(youtube(thumbnail_concepts=["A game-changer laptop"]))
    assert "BANNED_PHRASE" in fails(qa(Platform.YOUTUBE, ContentFormat.SHORT, thumb))


# ------------------------------------------------------------------ Instagram
@pytest.mark.parametrize(
    ("fmt", "frames", "code"),
    [
        (ContentFormat.REEL_SCRIPT, 2, "TOO_FEW_PARTS"),
        (ContentFormat.CAROUSEL, 1, "TOO_FEW_PARTS"),
        (ContentFormat.CAROUSEL, 11, "TOO_MANY_PARTS"),
        (ContentFormat.STORY, 0, "TOO_FEW_PARTS"),
        (ContentFormat.IMAGE_POST, 2, "TOO_MANY_PARTS"),
    ],
)
def test_instagram_frame_counts_per_format(fmt: ContentFormat, frames: int, code: str) -> None:
    fields = fields_from_output(instagram(frames=frames))
    assert code in fails(qa(Platform.INSTAGRAM, fmt, fields))


def test_instagram_caption_hook_and_frames() -> None:
    fields = fields_from_output(instagram(frames=4))
    draft = build_draft(Platform.INSTAGRAM, ContentFormat.CAROUSEL, fields)
    (caption,) = draft.segments
    assert caption.startswith("Your client data can stay in the office.\n\n" + SENTENCE)
    assert caption.rstrip().endswith("#LocalAI #SmallBusiness #Privacy")
    assert draft.parts[0] == "Slide 0 background | Point 0"
    assert fields.media_requirements == ["Clean desk shots, warm light, brand teal accents."]
    long_hook = fields_from_output(instagram(hook="Your data " * 14))
    results = qa(Platform.INSTAGRAM, ContentFormat.CAROUSEL, long_hook)
    assert "HOOK_TRUNCATED" in codes(results) and "HOOK_TRUNCATED" not in fails(results)
    long_caption = fields_from_output(instagram(caption=SENTENCE * 30))
    assert "OVER_LENGTH" in fails(qa(Platform.INSTAGRAM, ContentFormat.CAROUSEL, long_caption))
    banned = instagram(frames=3)
    banned.frames[1].text = "This is a game-changer"
    assert "BANNED_PHRASE" in fails(
        qa(Platform.INSTAGRAM, ContentFormat.STORY, fields_from_output(banned))
    )


# ------------------------------------------------------------------ Facebook
def test_facebook_composes_like_a_native_post() -> None:
    fields = fields_from_output(facebook(media_suggestion="Photo of the office desk"))
    draft = build_draft(Platform.FACEBOOK, ContentFormat.IMAGE_POST, fields)
    (text,) = draft.segments
    assert text.split("\n\n")[0] == "Would you let an AI read your client files?"
    assert CTA in text and text.rstrip().endswith("#LocalAI")
    assert fields.media_requirements == ["Photo of the office desk"]
    many = fields_from_output(facebook(hashtags=["a1", "b2", "c3", "d4"]))
    assert "TOO_MANY_HASHTAGS" in codes(qa(Platform.FACEBOOK, ContentFormat.TEXT_POST, many))


# ------------------------------------------------------------------ Reddit
def test_reddit_is_title_plus_self_post_without_hashtags() -> None:
    fields = fields_from_output(reddit(cta="Happy to share our checklist"))
    assert fields.extras["subreddit"] == "LocalLLaMA"
    assert fields.hook == fields.title
    tagged = VersionFields(
        hook=fields.hook,
        body=fields.body,
        cta=fields.cta,
        hashtags=["#LocalAI"],
        title=fields.title,
        extras=fields.extras,
    )
    draft = build_draft(Platform.REDDIT, ContentFormat.DISCUSSION, tagged)
    (text,) = draft.segments
    assert "#LocalAI" not in text and draft.hashtags == ()
    assert text.startswith(SENTENCE)  # the title is published separately
    assert text.rstrip().endswith("Happy to share our checklist")
    assert draft.community == "LocalLLaMA" and draft.flair == "Discussion"
    results = qa(Platform.REDDIT, ContentFormat.DISCUSSION, tagged, subreddits=(FRESH,))
    assert "MARKDOWN_NOT_RENDERED" not in codes(results)  # Reddit renders markdown


def test_reddit_needs_a_configured_subreddit() -> None:
    fields = fields_from_output(reddit())
    results = qa(Platform.REDDIT, ContentFormat.DISCUSSION, fields)
    assert "NO_SUBREDDIT_PROFILE" in codes(results)
    assert not fails(results)
    other = SubredditProfile(subreddit="smallbusiness", last_reviewed_at=datetime.now(UTC))
    assert "UNKNOWN_SUBREDDIT" in fails(
        qa(Platform.REDDIT, ContentFormat.DISCUSSION, fields, subreddits=(other,))
    )


@pytest.mark.parametrize(
    ("profile", "output", "code"),
    [
        (
            FRESH.model_copy(update={"banned_phrases": ["client files"]}),
            reddit(),
            "SUBREDDIT_BANNED_PHRASE",
        ),
        (FRESH.model_copy(update={"flair_required": True}), reddit(flair=None), "FLAIR_REQUIRED"),
        (FRESH, reddit(flair="Meme"), "UNKNOWN_FLAIR"),
        (
            FRESH.model_copy(update={"title_prefix": "[Discussion]"}),
            reddit(),
            "TITLE_PREFIX_MISSING",
        ),
        (FRESH.model_copy(update={"max_title_chars": 20}), reddit(), "TITLE_TOO_LONG"),
        (
            FRESH.model_copy(update={"self_promotion_policy": "BANNED"}),
            reddit(cta="Book a call"),
            "SELF_PROMOTION_BANNED",
        ),
        (FRESH.model_copy(update={"text_posts_allowed": False}), reddit(), "POST_TYPE_NOT_ALLOWED"),
    ],
)
def test_per_subreddit_rules_fail(profile: SubredditProfile, output: RedditPost, code: str) -> None:
    results = qa(
        Platform.REDDIT,
        ContentFormat.DISCUSSION,
        fields_from_output(output),
        subreddits=(profile,),
    )
    assert code in fails(results)


def test_subreddit_disclosure_and_staleness_warn() -> None:
    stale = FRESH.model_copy(
        update={
            "disclosure_required": True,
            "last_reviewed_at": datetime.now(UTC) - timedelta(days=120),
        }
    )
    results = qa(
        Platform.REDDIT, ContentFormat.DISCUSSION, fields_from_output(reddit()), subreddits=(stale,)
    )
    assert {"DISCLOSURE_REQUIRED", "SUBREDDIT_PROFILE_STALE"} <= codes(results)
    assert not fails(results)


# ------------------------------------------------------------------ Substack
def test_substack_formats_titles_and_notes() -> None:
    fields = fields_from_output(substack())
    draft = build_draft(Platform.SUBSTACK, ContentFormat.NEWSLETTER, fields)
    (text,) = draft.segments
    assert text.startswith("Most small teams send client files")
    assert "## The case" in text and text.rstrip().endswith(CTA)
    assert draft.title == "Why your AI should live in your office"
    assert "A practical case for local models" in draft.notes
    results = qa(Platform.SUBSTACK, ContentFormat.NEWSLETTER, fields)
    assert "MARKDOWN_NOT_RENDERED" not in codes(results)
    untitled = fields_from_output(substack(title=None))
    assert "MISSING_TITLE" in fails(qa(Platform.SUBSTACK, ContentFormat.ARTICLE, untitled))
    assert "MISSING_TITLE" not in fails(qa(Platform.SUBSTACK, ContentFormat.NOTE, untitled))
    magnet = fields_from_output(substack(lead_magnet="A game-changer checklist"))
    assert "BANNED_PHRASE" in fails(qa(Platform.SUBSTACK, ContentFormat.NEWSLETTER, magnet))


# ------------------------------------------------------------------ Rumble
def test_rumble_has_no_chapters_or_pinned_comment() -> None:
    with pytest.raises(ValidationError):
        rumble(chapters=[{"at": "0:00", "title": "Intro"}])
    fields = fields_from_output(rumble())
    assert set(fields.extras) == {"title_candidates", "sections", "thumbnail_concepts"}
    assert "MISSING_TITLE" in fails(
        qa(
            Platform.RUMBLE,
            ContentFormat.SHORT,
            VersionFields(hook="Hook here", body=fields.body, cta=CTA, hashtags=[]),
        )
    )


# ------------------------------------------------------------------ Mastodon
def test_mastodon_thread_limits_follow_the_instance() -> None:
    post = ("Local models keep client data on the machine. " * 9).strip()  # ~440 characters
    assert 400 < len(post) < 460
    fields = fields_from_output(mastodon(posts=[post, "Short closing post."]))
    assert "OVER_LENGTH" not in fails(qa(Platform.MASTODON, ContentFormat.THREAD, fields))
    strict = qa(Platform.MASTODON, ContentFormat.THREAD, fields, max_chars_per_post=400)
    assert "OVER_LENGTH" in fails(strict)
    single = fields_from_output(mastodon())
    assert "NOT_A_SINGLE_POST" in fails(qa(Platform.MASTODON, ContentFormat.TEXT_POST, single))
    lone = fields_from_output(mastodon(posts=[SENTENCE]))
    assert "THREAD_TOO_SHORT" in fails(qa(Platform.MASTODON, ContentFormat.THREAD, lone))


def test_mastodon_content_warning_is_kept_apart_but_counted() -> None:
    fields = fields_from_output(mastodon(content_warning="AI discussion"))
    assert fields.extras == {"content_warning": "AI discussion"}
    draft = build_draft(Platform.MASTODON, ContentFormat.THREAD, fields)
    assert all("AI discussion" not in s for s in draft.segments)
    assert draft.content_warning == "AI discussion"
    assert draft.segments[-1].rstrip().endswith(f"{CTA}\n\n#LocalAI")
    # The instance counts the warning inside each post's limit (review m2).
    post = "x" * 490
    near = fields_from_output(mastodon(posts=[post, "Short closing post."]))
    assert "OVER_LENGTH" not in fails(qa(Platform.MASTODON, ContentFormat.THREAD, near))
    warned = fields_from_output(
        mastodon(posts=[post, "Short closing post."], content_warning="A twenty-char CW...")
    )
    assert "OVER_LENGTH" in fails(qa(Platform.MASTODON, ContentFormat.THREAD, warned))
    banned = fields_from_output(mastodon(content_warning="game-changer ahead"))
    assert "BANNED_PHRASE" in fails(qa(Platform.MASTODON, ContentFormat.THREAD, banned))


def test_review_minor_fixes() -> None:
    # Subreddit names are normalised the same way everywhere (review m1).
    merged = merge_extras(Platform.REDDIT, {}, {"subreddit": "/r/LocalLLaMA"})
    assert merged["subreddit"] == "LocalLLaMA"
    upper = fields_from_output(reddit(subreddit="R/localllama"))
    assert "UNKNOWN_SUBREDDIT" not in fails(
        qa(Platform.REDDIT, ContentFormat.DISCUSSION, upper, subreddits=(FRESH,))
    )
    # A Substack Note has a length limit (m3).
    note = fields_from_output(substack(title=None, subtitle=None, body=SENTENCE * 15))
    assert "OVER_LENGTH" in fails(qa(Platform.SUBSTACK, ContentFormat.NOTE, note))
    assert "OVER_LENGTH" not in fails(
        qa(
            Platform.SUBSTACK,
            ContentFormat.NEWSLETTER,
            fields_from_output(substack(body=SENTENCE * 15)),
        )
    )
    # Self-promotion written into the body counts as promotion (m4).
    body = f"{SENTENCE}\n\nBook a call with us. Anyone else run local models for clients?"
    promo = fields_from_output(reddit(body=body, cta=None))
    banned = FRESH.model_copy(update={"self_promotion_policy": "BANNED"})
    assert "SELF_PROMOTION_BANNED" in fails(
        qa(
            Platform.REDDIT,
            ContentFormat.DISCUSSION,
            promo,
            subreddits=(banned,),
            expected_cta="Book a call with us",
        )
    )
    # "#1" is not a hashtag (m5).
    ranked = fields_from_output(reddit(body=f"The #1 mistake: {SENTENCE} What do you run?"))
    assert "TOO_MANY_HASHTAGS" not in codes(
        qa(Platform.REDDIT, ContentFormat.DISCUSSION, ranked, subreddits=(FRESH,))
    )


# ------------------------------------------------------------------ human edits of extras
def test_platform_field_edits_are_merged_and_validated() -> None:
    base = fields_from_output(youtube()).payload
    merged = merge_extras(Platform.YOUTUBE, base, {"pinned_comment": "New question?"})
    assert merged["pinned_comment"] == "New question?"
    assert merged["title_candidates"] == base["title_candidates"]  # untouched keys kept
    assert "hashtags" not in merged  # common keys are not extras
    with pytest.raises(OmniGrowthError) as bad:
        merge_extras(Platform.INSTAGRAM, {}, {"frames": [{"text": "no visual"}]})
    assert bad.value.code is ErrorCode.VALIDATION_FAILURE
    with pytest.raises(OmniGrowthError) as none:
        merge_extras(Platform.LINKEDIN, {}, {"subtitle": "x"})
    assert none.value.code is ErrorCode.VALIDATION_FAILURE
    assert merge_extras(Platform.LINKEDIN, {"hashtags": ["#a"]}, None) == {}


# ------------------------------------------------------------------ account settings
def test_account_settings_are_validated_per_platform() -> None:
    settings = validate_settings(
        Platform.REDDIT,
        {"subreddits": [{"subreddit": "r/LocalLLaMA", "banned_phrases": ["DM me"]}]},
    )
    assert settings["subreddits"][0]["subreddit"] == "LocalLLaMA"
    with pytest.raises(OmniGrowthError):
        validate_settings(
            Platform.REDDIT, {"subreddits": [{"subreddit": "a_b"}, {"subreddit": "A_B"}]}
        )
    with pytest.raises(OmniGrowthError):
        validate_settings(Platform.REDDIT, {"subreddits": [{"subreddit": "a", "typo": 1}]})
    with pytest.raises(OmniGrowthError):
        validate_settings(Platform.LINKEDIN, {"anything": 1})
    assert validate_settings(Platform.LINKEDIN, None) == {}
    assert mastodon_max_characters([{"max_characters": 500}, {"max_characters": 1000}, {}]) == 500
    assert mastodon_max_characters([{}]) is None
    profiles = subreddit_profiles(
        [
            {"subreddits": [{"subreddit": "LocalLLaMA"}]},
            {"subreddits": [{"subreddit": "localllama"}]},
        ]
    )
    assert [p.subreddit for p in profiles] == ["LocalLLaMA"]
