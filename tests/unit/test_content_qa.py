"""Unit tests: QA stages 1-4 (spec §11) and writer composition (Phase 6 gate)."""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigrowth.agents.writers import (
    POST_SEPARATOR,
    LinkedInPost,
    VersionFields,
    XPost,
    build_draft,
    fields_from_output,
)
from omnigrowth.content.qa import (
    ClaimRef,
    Draft,
    QaContext,
    SimilarHit,
    load_qa_config,
    overall_score,
    run_qa,
)
from omnigrowth.core.enums import ClaimLabel, ContentFormat, Platform, QaOutcome, QaStage
from omnigrowth.providers.fake import hashing_embedding

REPO = Path(__file__).resolve().parents[2]
CONFIG = load_qa_config(REPO / "config")
EVIDENCE = (
    "Local AI agents keep proprietary data on the machine. A 9B model runs on a GPU with "
    "12 GB of VRAM. Routine work avoids per-token cloud fees."
)
CLAIMS = {
    1: ClaimRef(ClaimLabel.SOURCE_STATED, "Data stays on the machine."),
    2: ClaimRef(ClaimLabel.OPINION, "Hybrid will win."),
}


def ctx(**overrides: object) -> QaContext:
    base: dict[str, object] = {
        "cta_required": True,
        "banned_phrases": ("game-changer", "synergy"),
        "avoid_terms": ("revolutionary",),
        "claims": CLAIMS,
        "evidence_text": EVIDENCE,
        "allowed_urls": frozenset({"https://simplyai.example/call"}),
    }
    base.update(overrides)
    return QaContext(**base)  # type: ignore[arg-type]


def linkedin(**overrides: object) -> Draft:
    fields = VersionFields(
        hook="Your client data does not need to leave your office.",
        body=(
            "Local AI agents keep proprietary data on the machine. A 9B model now runs on a "
            "GPU with 12 GB of VRAM, which covers drafting and ticket summaries.\n\n"
            "Routine work avoids per-token cloud fees when it runs locally."
        ),
        cta="Book a free 20-minute readiness call: https://simplyai.example/call",
        hashtags=["#LocalAI", "SmallBusiness"],
        cited_claims=[1],
    )
    for key, value in overrides.items():
        object.__setattr__(fields, key, value)
    return build_draft(Platform.LINKEDIN, ContentFormat.TEXT_POST, fields)


def outcomes(draft: Draft, context: QaContext | None = None) -> dict[QaStage, QaOutcome]:
    return {r.stage: r.outcome for r in run_qa(draft, context or ctx(), CONFIG)}


def codes(draft: Draft, context: QaContext | None = None) -> set[str]:
    return {f.code for r in run_qa(draft, context or ctx(), CONFIG) for f in r.findings}


# ------------------------------------------------------------------ happy path
def test_clean_linkedin_post_passes_every_stage() -> None:
    results = run_qa(linkedin(), ctx(), CONFIG)
    assert [r.stage for r in results] == list(QaStage)
    assert all(r.outcome is QaOutcome.PASS for r in results), [
        (r.stage, r.findings) for r in results
    ]
    assert overall_score(results) == 100


# ------------------------------------------------------------------ gate: four rejections
def test_missing_cta_is_rejected_when_required() -> None:
    draft = linkedin(cta=None)
    assert outcomes(draft)[QaStage.STRUCTURAL] is QaOutcome.FAIL
    assert "MISSING_CTA" in codes(draft)
    # Not required -> not a finding.
    assert "MISSING_CTA" not in codes(draft, ctx(cta_required=False))


def test_banned_phrase_is_rejected_case_insensitively() -> None:
    draft = linkedin(hook="Local AI is a Game-Changer for small teams.")
    assert outcomes(draft)[QaStage.BRAND] is QaOutcome.FAIL
    assert "BANNED_PHRASE" in codes(draft)
    # Word boundaries: "synergy" inside "synergystic" is not a hit, avoid terms only warn.
    ok = linkedin(hook="A revolutionary but honest look at local AI.")
    assert outcomes(ok)[QaStage.BRAND] is QaOutcome.WARN


def test_citation_not_in_research_packet_is_rejected() -> None:
    draft = linkedin(body=linkedin().body + "\n\nSource: https://made-up-stats.example/report")
    assert outcomes(draft)[QaStage.ACCURACY] is QaOutcome.FAIL
    finding = next(
        f
        for r in run_qa(draft, ctx(), CONFIG)
        for f in r.findings
        if f.code == "CITATION_NOT_IN_PACKET"
    )
    assert finding.detail["url"] == "https://made-up-stats.example/report"
    # The same link is fine once it is a packet citation.
    cited = ctx(
        allowed_urls=frozenset(
            {"https://simplyai.example/call", "https://made-up-stats.example/report/"}
        )
    )
    assert "CITATION_NOT_IN_PACKET" not in codes(draft, cited)


def test_over_length_x_post_is_rejected() -> None:
    long_post = "Local AI agents keep proprietary data on the machine. " * 6  # ~330 chars
    fields = fields_from_output(XPost(posts=[long_post], cta="Book a call"))
    draft = build_draft(Platform.X, ContentFormat.TEXT_POST, fields)
    assert len(draft.segments[0]) > 280
    results = {r.stage: r for r in run_qa(draft, ctx(), CONFIG)}
    assert results[QaStage.PLATFORM].outcome is QaOutcome.FAIL
    over = [f for f in results[QaStage.PLATFORM].findings if f.code == "OVER_LENGTH"]
    assert over and over[0].detail["post"] == 1 and over[0].detail["max"] == 280


def test_cta_appended_to_last_x_post_counts_toward_the_limit() -> None:
    post = "x" * 270
    fields = fields_from_output(XPost(posts=["Hook line here", post], cta="Book a call now"))
    draft = build_draft(Platform.X, ContentFormat.THREAD, fields)
    assert draft.segments[-1].endswith("Book a call now")
    assert "OVER_LENGTH" in codes(draft)


# ------------------------------------------------------------------ other rules
def test_unsupported_statistics_and_unknown_claims_fail_accuracy() -> None:
    draft = linkedin(body=linkedin().body + " Teams save 80% in year one.", cited_claims=[1, 9])
    found = codes(draft)
    assert {"UNSUPPORTED_STATISTIC", "UNKNOWN_CLAIM"} <= found
    # A figure present in the evidence is supported.
    assert "UNSUPPORTED_STATISTIC" not in codes(linkedin())


def test_opinion_claim_and_unsourced_quote_only_warn() -> None:
    quote = '"Every company will run its own model on its own hardware by next spring"'
    draft = linkedin(body=linkedin().body + "\n\n" + quote, cited_claims=[2])
    stage = {r.stage: r for r in run_qa(draft, ctx(), CONFIG)}[QaStage.ACCURACY]
    assert stage.outcome is QaOutcome.WARN
    assert {f.code for f in stage.findings} == {"CITES_UNVERIFIED_CLAIM", "UNSOURCED_QUOTE"}


def test_platform_checks_format_hashtags_markdown_and_urls() -> None:
    draft = linkedin(
        body="**Bold claim** here.\n\nSee https://brokenlink/page for more.",
        hashtags=["#AI", "#ai", "#one", "#two", "#three", "#four", "#five"],
    )
    found = codes(draft)
    assert {
        "DUPLICATE_HASHTAG",
        "TOO_MANY_HASHTAGS",
        "MARKDOWN_NOT_RENDERED",
        "BROKEN_URL",
    } <= found
    reel = build_draft(
        Platform.X,
        ContentFormat.REEL_SCRIPT,
        VersionFields(hook="h", body="Short post", cta="Call", hashtags=[]),
    )
    assert "UNSUPPORTED_FORMAT" in codes(reel)


def test_thread_shape_is_structural() -> None:
    one = fields_from_output(XPost(posts=["Only one post in this thread"], cta="Follow"))
    assert "THREAD_TOO_SHORT" in codes(build_draft(Platform.X, ContentFormat.THREAD, one))
    two = fields_from_output(XPost(posts=["First post", "Second post"], cta="Follow"))
    assert "NOT_A_SINGLE_POST" in codes(build_draft(Platform.X, ContentFormat.TEXT_POST, two))


# ------------------------------------------------------------------ §10 duplicate / fatigue
def test_similarity_hits_warn_but_never_fail() -> None:
    hits = (
        SimilarHit("FULL", "a1", "v1", 0.95),
        SimilarHit("HOOK", "a2", "v2", 0.97),
        SimilarHit("FULL", "a3", "v3", 0.50),
    )
    stage = {r.stage: r for r in run_qa(linkedin(), ctx(similar=hits), CONFIG)}[QaStage.BRAND]
    assert stage.outcome is QaOutcome.WARN
    assert sorted(f.code for f in stage.findings) == ["NEAR_DUPLICATE", "REPEATED_HOOK"]


def test_fake_embeddings_rank_near_duplicates_above_threshold() -> None:
    a = "Local AI agents keep your client data on your own machine and cut cloud fees."
    b = "Local AI agents keep your client data on your own machine and cut cloud costs."
    c = "Our spring webinar covers hiring, payroll and onboarding for new staff."

    def cos(x: str, y: str) -> float:
        return sum(p * q for p, q in zip(hashing_embedding(x), hashing_embedding(y), strict=True))

    assert cos(a, b) >= CONFIG.similarity.near_duplicate
    assert cos(a, c) < 0.5


# ------------------------------------------------------------------ composition
def test_linkedin_composition_does_not_repeat_the_cta() -> None:
    out = LinkedInPost(
        hook="Hook line for the post",
        body="Body text that already ends with Book a call.",
        cta="Book a call.",
        hashtags=["local ai", "#SMB"],
    )
    draft = build_draft(Platform.LINKEDIN, ContentFormat.TEXT_POST, fields_from_output(out))
    assert draft.segments[0].count("Book a call.") == 1
    assert draft.segments[0].endswith("#localai #SMB")


def test_x_thread_round_trips_through_the_stored_body() -> None:
    fields = fields_from_output(XPost(posts=["One", "Two", "Three"], hashtags=["AI"]))
    assert fields.body == POST_SEPARATOR.join(["One", "Two", "Three"])
    draft = build_draft(Platform.X, ContentFormat.THREAD, fields)
    assert draft.segments == ("One", "Two", "Three\n\n#AI")
    assert draft.hook == "One"


def test_qa_config_rejects_bad_files(tmp_path: Path) -> None:
    (tmp_path / "qa.yaml").write_text("similarity: {near_duplicate: 2}\n", encoding="utf-8")
    with pytest.raises(Exception, match=r"qa\.yaml"):
        load_qa_config(tmp_path)


# ------------------------------------------------------------------ review regressions
def test_statistics_are_compared_as_whole_figures() -> None:
    evidence = "Adoption grew 35% and costs fell 12x to $500 per month."
    context = ctx(evidence_text=evidence)
    for invented in ("5%", "2x", "$5"):
        draft = linkedin(body=linkedin().body + f" We saw {invented} in our pilot.")
        assert "UNSUPPORTED_STATISTIC" in codes(draft, context), invented
    for real in ("35%", "12x", "$500"):
        draft = linkedin(body=linkedin().body + f" We saw {real} in our pilot.")
        assert "UNSUPPORTED_STATISTIC" not in codes(draft, context), real


def test_bare_domains_are_checked_and_citations_compare_by_host_and_path() -> None:
    bare = linkedin(body=linkedin().body + " Full report at made-up-stats.com/report.")
    assert "CITATION_NOT_IN_PACKET" in codes(bare)
    allowed = ctx(allowed_urls=frozenset({"https://example.com/report"}))
    for spelling in (
        "www.example.com/report",
        "http://example.com/report/",
        "https://example.com/report?utm_source=li",
    ):
        draft = linkedin(cta=None, body=linkedin().body + f" Source: {spelling}")
        assert "CITATION_NOT_IN_PACKET" not in codes(draft, allowed), spelling
    other_path = linkedin(cta=None, body=linkedin().body + " https://example.com/Other")
    assert "CITATION_NOT_IN_PACKET" in codes(other_path, allowed)
    email = linkedin(cta=None, body=linkedin().body + " Write to hello@simplyai.com today.")
    assert "CITATION_NOT_IN_PACKET" not in codes(email, ctx(cta_required=False))


def test_cta_written_into_the_text_satisfies_the_requirement() -> None:
    cta = "Book a free 20-minute readiness call"
    inline = linkedin(cta=None, body=linkedin().body + f"\n\n{cta}.")
    assert "MISSING_CTA" not in codes(inline, ctx(expected_cta=cta))
    assert "MISSING_CTA" in codes(linkedin(cta=None), ctx(expected_cta=cta))


def test_linkedin_composition_dedupes_hook_and_hashtags() -> None:
    fields = VersionFields(
        hook="Your data can stay home.",
        body="Your data can stay home.\n\nMore text about #LocalAI and why it matters.",
        cta=None,
        hashtags=["#LocalAI", "#SMB"],
    )
    text = build_draft(Platform.LINKEDIN, ContentFormat.TEXT_POST, fields).segments[0]
    assert text.count("Your data can stay home.") == 1
    assert text.count("#LocalAI") == 1 and text.endswith("#SMB")


def test_x_hashtags_match_whole_tags_across_all_posts() -> None:
    fields = fields_from_output(
        XPost(posts=["First post about #AIethics", "Second post"], hashtags=["AI", "AIethics"])
    )
    draft = build_draft(Platform.X, ContentFormat.THREAD, fields)
    assert draft.segments[-1].endswith("#AI")  # #AI is not "present" inside #AIethics
    assert "#AIethics" not in draft.segments[-1]  # already in post 1
