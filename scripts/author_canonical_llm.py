"""Author the hand-written canonical LLM answers used to record planning fixtures (A8).

Writes ``tests/fixtures/llm_canonical/<prompt_id with / -> __>.json``. These answers are
consistent with ``tests/integration/test_campaign_planning.py::ARTICLE``; the recorded
FakeProvider fixtures (``tests/fixtures/llm``) are produced from them by running that test
with ``OMNIGROWTH_RECORD_FIXTURES=1``.

    uv run python scripts/author_canonical_llm.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "llm_canonical"


def write(name: str, responses: list[Any]) -> None:
    body = json.dumps({"responses": responses}, indent=2, ensure_ascii=False) + "\n"
    (OUT / name).write_text(body, encoding="utf-8")


RESEARCH = {
    "summary": (
        "The source argues that local AI agents let small businesses keep proprietary data on "
        "their own hardware and run routine drafting and classification on a 12 GB consumer "
        "GPU without per-token fees, while conceding that frontier models still lead at long "
        "multi-step reasoning."
    ),
    "claims": [
        {
            "claim": "Local AI agents keep proprietary data on the machine instead of a cloud.",
            "label": "SOURCE_STATED",
            "source_excerpt": "Local AI agents keep proprietary data on the machine",
        },
        {
            "claim": "A 9B-parameter model is practical on a consumer GPU with 12 GB of VRAM.",
            "label": "SOURCE_STATED",
            "source_excerpt": (
                "Running a 9B-parameter model on a consumer GPU with 12 GB of VRAM is now practical"
            ),
        },
        {
            "claim": "Running routine work such as ticket summaries locally avoids per-token fees.",
            "label": "SOURCE_STATED",
            "source_excerpt": "avoids per-token cloud fees",
        },
        {
            "claim": "Local models are weaker than frontier models at long, multi-step reasoning.",
            "label": "SOURCE_STATED",
            "source_excerpt": (
                "Local models are still weaker than frontier models at long, multi-step reasoning"
            ),
        },
        {
            # Fabricated quote: the A5 provenance check must downgrade it.
            "claim": "Switching to local AI cuts a small business's AI costs by 80%.",
            "label": "SOURCE_STATED",
            "source_excerpt": "cuts AI costs by 80%",
        },
        {
            "claim": "Most small teams will run a hybrid local/cloud setup within two years.",
            "label": "OPINION",
            "source_excerpt": None,
        },
        {
            "claim": "Cloud LLM API prices keep falling every year.",
            "label": "NEEDS_VERIFICATION",
            "source_excerpt": None,
        },
    ],
    "opposing_perspectives": [
        "Cloud providers offer contractual data protection that may be enough for most teams.",
        "Hardware, power and maintenance can outweigh per-token fees at low volumes.",
    ],
    "open_questions": [
        "What does a local setup cost per month for a five-person team?",
        "Which everyday tasks are good enough on a 9B model?",
        "How do you decide what stays local and what goes to the cloud?",
    ],
}

STRATEGY = {
    "positioning": (
        "Simply AI Solutions is the practical guide for small businesses that want AI help "
        "without handing their data to a cloud vendor: local-first, honest about limits, "
        "hybrid when it pays."
    ),
    "audience_summary": (
        "Owners and operations leads of 5-50 person businesses who handle client data and are "
        "wary of cloud AI costs and privacy."
    ),
    "content_pillars": ["Data stays home", "Real costs of AI", "What local models can do"],
    "key_messages": [
        "Your proprietary data can stay on your own machine.",
        "Routine AI work does not need per-token cloud fees.",
        "Use frontier models only where they clearly win.",
    ],
    "funnel_stage": "AWARENESS",
    "primary_cta": "Book a free 20-minute local-AI readiness call",
    "primary_platform": "LINKEDIN",
    # REDDIT is not a campaign platform: the strategy agent's output must be clamped.
    "secondary_platforms": ["X", "REDDIT"],
    "rationale": (
        "LinkedIn reaches decision makers at small businesses; X extends reach for the "
        "contrarian cost angle."
    ),
}

HOOKS = {
    "EDUCATIONAL": [
        "What a local AI agent actually is (and what it is not)",
        "How a 9B model fits on a 12 GB GPU, explained in plain English",
        "Three tasks your team can move to a local model this week",
    ],
    "CONTRARIAN": [
        "Stop paying per token for work a desktop GPU can do",
        "The cloud is not the safe default for your client data",
        "Bigger models are not better for most small-business tasks",
    ],
    "STORY": [
        "The day we pulled our client files back from the cloud",
        "How a five-person agency replaced its ticket-summary subscription",
        "What went wrong the first time we ran AI locally",
    ],
    "TACTICAL": [
        "A 30-minute checklist to run your first local AI agent",
        "Decide local vs. cloud per task with this two-question rule",
        "Set up private ticket summaries without sending data anywhere",
    ],
    "COMPARISON": [
        "Local 9B model vs. frontier API: where each one wins",
        "Per-token fees vs. a one-off GPU: a side-by-side view",
        "Hybrid vs. all-cloud AI for a ten-person team",
    ],
    "OPINION": [
        "Most small teams will run hybrid AI within two years",
        "Privacy should decide your AI setup before price does",
        "AI vendors undersell how good small local models have become",
    ],
    "DATA_DRIVEN": [
        "12 GB of VRAM is now enough for everyday drafting work",
        "What per-token fees add up to for routine ticket summaries",
        "Where local models still fall short on multi-step reasoning",
    ],
    "CASE_STUDY": [
        "Case study: an accounting firm keeps client data on-site with local AI",
        "Case study: a support team summarises tickets locally",
        "Case study: when a small team chose a frontier model on purpose",
    ],
}
VALUES = {
    "EDUCATIONAL": [0.72, 0.64, 0.61],
    "CONTRARIAN": [0.81, 0.77, 0.58],
    "STORY": [0.69, 0.66, 0.55],
    "TACTICAL": [0.84, 0.71, 0.68],
    "COMPARISON": [0.75, 0.73, 0.52],
    "OPINION": [0.63, 0.6, 0.5],
    "DATA_DRIVEN": [0.7, 0.62, 0.57],
    "CASE_STUDY": [0.79, 0.67, 0.54],
}
EVIDENCE = {
    "EDUCATIONAL": [2],
    "CONTRARIAN": [3],
    "STORY": [1],
    "TACTICAL": [2, 3],
    "COMPARISON": [2, 4],
    "OPINION": [6],
    "DATA_DRIVEN": [2, 4],
    "CASE_STUDY": [1, 3],
}
PLATFORMS = [["LINKEDIN", "X"], ["LINKEDIN"], ["X", "YOUTUBE"]]  # YOUTUBE gets dropped


def ideas() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for angle, hooks in HOOKS.items():
        for i, hook in enumerate(hooks):
            out.append(
                {
                    "angle": angle,
                    "hook": hook,
                    "summary": f"Post for small-business owners: {hook.lower()}.",
                    "platform_targets": PLATFORMS[i],
                    "estimated_value": VALUES[angle][i],
                    # Claim 99 does not exist: must be filtered out.
                    "evidence_claims": EVIDENCE[angle] + ([99] if i == 2 else []),
                }
            )
    return out


def plan_item(
    day: int,
    ref: str,
    platform: str,
    fmt: str,
    angle: str,
    objective: str,
    stage: str,
    cta: str | None,
    hour: int,
    hours: int,
    depends_on: int | None = None,
) -> dict[str, Any]:
    return {
        "day": day,
        "idea_ref": ref,
        "platform": platform,
        "format": fmt,
        "angle": angle,
        "objective": objective,
        "funnel_stage": stage,
        "cta": cta,
        "window_start_hour": hour,
        "window_hours": hours,
        "depends_on": depends_on,
    }


CALL = "Book a free 20-minute local-AI readiness call"
PLAN = {
    "items": [
        plan_item(
            1,
            "I1",
            "LINKEDIN",
            "TEXT_POST",
            "TACTICAL",
            "AUTHORITY",
            "AWARENESS",
            "Follow for the full local-AI checklist series",
            8,
            3,
        ),
        plan_item(
            2,
            "I2",
            "X",
            "THREAD",
            "CONTRARIAN",
            "REACH",
            "AWARENESS",
            "Repost if your team pays per token",
            12,
            2,
        ),
        plan_item(
            3,
            "I3",
            "LINKEDIN",
            "CAROUSEL",
            "CASE_STUDY",
            "AUTHORITY",
            "CONSIDERATION",
            CALL,
            9,
            3,
            depends_on=1,
        ),
        plan_item(
            3, "I2", "X", "TEXT_POST", "CONTRARIAN", "REACH", "AWARENESS", None, 17, 2, depends_on=2
        ),
        plan_item(
            4,
            "I4",
            "LINKEDIN",
            "DOCUMENT_CAROUSEL",
            "COMPARISON",
            "AUTHORITY",
            "CONSIDERATION",
            "Comment HYBRID for the comparison sheet",
            8,
            4,
        ),
        plan_item(
            5,
            "I5",
            "LINKEDIN",
            "TEXT_POST",
            "EDUCATIONAL",
            "FOLLOWERS",
            "AWARENESS",
            "Follow for plain-English AI explainers",
            10,
            3,
        ),
        plan_item(6, "I6", "X", "THREAD", "DATA_DRIVEN", "REACH", "AWARENESS", None, 11, 3),
        plan_item(
            7,
            "I7",
            "LINKEDIN",
            "TEXT_POST",
            "STORY",
            "LEADS",
            "CONVERSION",
            CALL,
            9,
            3,
            depends_on=3,
        ),
    ]
}

# ------------------------------------------------------------------ Phase 6 writers
# Generation order = plan order. LinkedIn items: day1, day3, day4, day5, day7; X items:
# day2 (thread), day3 (single post), day6 (thread). Each answer exercises one QA path.
_LI_BODY = (
    "Most small teams send client files to a cloud AI without thinking twice.\n\n"
    "Local AI agents keep proprietary data on the machine. A 9B-parameter model now runs on a "
    "consumer GPU with 12 GB of VRAM, which is enough for drafting and classification.\n\n"
    "Start with one routine task, such as summarising support tickets, and run it locally. "
    "Routine work avoids per-token cloud fees when it runs on your own hardware.\n\n"
    "Keep the long, multi-step reasoning jobs for frontier models where they clearly win."
)
LINKEDIN_POSTS = [
    {  # day 1: clean -> AWAITING_APPROVAL
        "hook": "Your client data does not need to leave your office to get AI help.",
        "body": _LI_BODY,
        "cta": "Follow for the full local-AI checklist series",
        "hashtags": ["LocalAI", "SmallBusiness"],
        "cited_claims": [1, 2, 3, 4],
        "hook_type": "statement",
        "cta_type": "follow",
        "media_suggestion": None,
    },
    {  # day 3: carousel script, clean
        "hook": "How an accounting firm kept client data on-site and still used AI.",
        "body": (
            "Slide 1: The problem - client files and cloud AI do not mix.\n\n"
            "Slide 2: Local AI agents keep proprietary data on the machine.\n\n"
            "Slide 3: A consumer GPU with 12 GB of VRAM runs a 9B-parameter model.\n\n"
            "Slide 4: Routine drafting runs locally, with no per-token cloud fees.\n\n"
            "Slide 5: Hard multi-step reasoning still goes to a frontier model."
        ),
        "cta": "Book a free 20-minute local-AI readiness call",
        "hashtags": ["LocalAI"],
        "cited_claims": [1, 2, 3, 4],
        "hook_type": "story",
        "cta_type": "call",
        "media_suggestion": "Five-slide PDF carousel, one idea per slide.",
    },
    {  # day 4: banned phrase -> QA_FAILED (BRAND)
        "hook": "Local AI is a game-changer for small teams. Here is the comparison.",
        "body": (
            "Local 9B model or frontier API? It depends on the task.\n\n"
            "Local AI agents keep proprietary data on the machine and avoid per-token cloud "
            "fees. Frontier models still lead at long, multi-step reasoning.\n\n"
            "Use both, on purpose."
        ),
        "cta": "Comment HYBRID for the comparison sheet",
        "hashtags": ["AI"],
        "cited_claims": [1, 3, 4],
        "hook_type": "contrarian",
        "cta_type": "comment",
        "media_suggestion": "Two-column comparison document.",
    },
    {  # day 5: near-duplicate of day 1 -> NEAR_DUPLICATE warning, still passes
        "hook": "Your client data does not need to leave your office to get AI help.",
        "body": _LI_BODY,
        "cta": "Follow for plain-English AI explainers",
        "hashtags": ["LocalAI", "SmallBusiness"],
        "cited_claims": [1, 2, 3, 4],
        "hook_type": "statement",
        "cta_type": "follow",
        "media_suggestion": None,
    },
    {  # day 7: missing CTA although the plan item requires one -> QA_FAILED (STRUCTURAL)
        "hook": "The day we pulled our client files back from the cloud.",
        "body": (
            "We had been pasting client documents into a cloud chatbot for months.\n\n"
            "Then we asked where that data lives. Local AI agents keep proprietary data on "
            "the machine, so we moved our drafting to a 9B-parameter model on a GPU with 12 "
            "GB of VRAM.\n\nIt handles the routine work. We keep a frontier model for the "
            "hard reasoning."
        ),
        "cta": None,
        "hashtags": [],
        "cited_claims": [1, 2],
        "hook_type": "story",
        "cta_type": "none",
        "media_suggestion": None,
    },
]
X_POSTS = [
    {  # day 2: clean thread
        "posts": [
            "Stop paying per token for work a desktop GPU can do.",
            "Local AI agents keep proprietary data on the machine.",
            "A 9B-parameter model runs on a consumer GPU with 12 GB of VRAM.",
            "Keep frontier models for long, multi-step reasoning.",
        ],
        "cta": "Repost if your team pays per token",
        "hashtags": ["LocalAI"],
        "cited_claims": [1, 2, 4],
        "hook_type": "contrarian",
        "cta_type": "comment",
    },
    {  # day 3: a single post that is far over the limit -> QA_FAILED (PLATFORM)
        "posts": [
            "The cloud is not the safe default for your client data. Local AI agents keep "
            "proprietary data on the machine, a 9B-parameter model runs on a consumer GPU "
            "with 12 GB of VRAM, routine work avoids per-token cloud fees, and you keep "
            "frontier models only for the long, multi-step reasoning jobs where they clearly "
            "win. That is the whole argument, and it fits on one desk."
        ],
        "cta": None,
        "hashtags": [],
        "cited_claims": [1, 2, 3, 4],
        "hook_type": "contrarian",
        "cta_type": "none",
    },
    {  # day 6: links a "source" that is not in the research packet -> QA_FAILED (ACCURACY)
        "posts": [
            "12 GB of VRAM is now enough for everyday drafting work.",
            "A 9B-parameter model covers drafting and classification on one consumer GPU.",
            "Full benchmark: https://llm-benchmarks.example/local-9b",
        ],
        "cta": None,
        "hashtags": [],
        "cited_claims": [2],
        "hook_type": "statistic",
        "cta_type": "none",
    },
]


# ------------------------------------------------------------------ Phase 10 writers
# YOUTUBE and INSTAGRAM answer the §55 campaign's items; the others answer repurposing
# requests in tests/integration/test_repurposing.py. All pass QA against the ARTICLE.
YOUTUBE_VIDEOS = [
    {
        "title_candidates": [
            "How One Firm Kept Client Data Off the Cloud",
            "Local AI on a 12 GB GPU: A Small-Firm Case Study",
            "Your Client Files Never Leave the Office",
        ],
        "title": "How One Firm Kept Client Data Off the Cloud",
        "hook": (
            "What if your AI assistant never saw the cloud at all? That is how one small "
            "firm uses it today."
        ),
        "sections": [
            {
                "heading": "The problem",
                "text": (
                    "Client files and cloud chatbots do not mix. Every paste sends private "
                    "data to someone else's servers."
                ),
            },
            {
                "heading": "The setup",
                "text": (
                    "Local AI agents keep proprietary data on the machine. A 9B-parameter "
                    "model runs on a consumer GPU with 12 GB of VRAM."
                ),
            },
            {
                "heading": "What runs locally",
                "text": (
                    "Routine drafting and ticket summaries run locally, which avoids "
                    "per-token cloud fees."
                ),
            },
            {
                "heading": "Where the cloud still wins",
                "text": (
                    "Frontier models still lead at long, multi-step reasoning, so the hard "
                    "jobs stay there."
                ),
            },
        ],
        "description": (
            "A small firm moved its routine AI work onto its own hardware so client data "
            "stays on the machine.\n\nWe walk through the setup, what runs locally, and "
            "where a frontier model is still the better tool."
        ),
        "chapters": [
            {"at": "0:00", "title": "The problem"},
            {"at": "1:15", "title": "The setup"},
            {"at": "3:40", "title": "What runs locally"},
            {"at": "6:05", "title": "Where the cloud still wins"},
        ],
        "thumbnail_concepts": ["Office desk with a padlocked laptop; overlay: DATA STAYS HOME"],
        "pinned_comment": "Which routine task would you move to a local model first?",
        "cta": None,
        "hashtags": ["LocalAI"],
        "cited_claims": [1, 2, 3, 4],
        "hook_type": "question",
        "cta_type": "comment",
    }
]
INSTAGRAM_POSTS = [
    {
        "hook": "Your AI does not need your client files. Swipe for the local setup.",
        "caption": (
            "Local AI agents keep proprietary data on the machine.\n\nA 9B-parameter model "
            "runs on a consumer GPU with 12 GB of VRAM, enough for routine drafting.\n\n"
            "Save this for your next tooling review."
        ),
        "frames": [
            {"visual": "Bold title slide, brand teal", "text": "Keep client data at home"},
            {"visual": "Icon of a laptop with a lock", "text": "Data stays on the machine"},
            {"visual": "GPU photo with a label", "text": "12 GB of VRAM runs a 9B model"},
            {"visual": "Checklist graphic", "text": "Drafting and summaries run locally"},
            {"visual": "Split screen local vs cloud", "text": "Hard reasoning: frontier model"},
        ],
        "visual_direction": "Flat illustrations, brand teal and white, one idea per slide.",
        "cta": None,
        "hashtags": ["LocalAI", "SmallBusiness", "DataPrivacy"],
        "cited_claims": [1, 2, 3, 4],
        "hook_type": "statement",
        "cta_type": "save",
    }
]
FACEBOOK_POSTS = [
    {
        "hook": "Would you paste a client file into a cloud chatbot?",
        "body": (
            "Many small teams do it every day without a second thought.\n\nLocal AI agents "
            "keep proprietary data on the machine, and routine drafting runs locally without "
            "per-token cloud fees.\n\nWhat is the first task you would keep in-house?"
        ),
        "cta": "Follow for the full local-AI checklist series",
        "hashtags": ["LocalAI"],
        "cited_claims": [1, 3],
        "hook_type": "question",
        "cta_type": "follow",
        "media_suggestion": None,
    }
]
REDDIT_POSTS = [
    {
        "subreddit": "LocalLLaMA",
        "title": "Running routine client drafting on a 9B model with 12 GB of VRAM",
        "body": (
            "We moved our routine drafting off cloud chatbots. Local agents keep proprietary "
            "data on the machine, which mattered more to our clients than we expected.\n\n"
            "- a 9B-parameter model on a consumer GPU with 12 GB of VRAM\n"
            "- ticket summaries and first drafts run locally, no per-token fees\n"
            "- long multi-step reasoning still goes to a frontier model\n\n"
            "What do you run locally, and where did you draw the line?"
        ),
        "flair": "Discussion",
        "cta": None,
        "cited_claims": [1, 2, 3, 4],
        "hook_type": "question",
    }
]
SUBSTACK_POSTS = [
    {
        "title": "Why your AI should live in your office",
        "subtitle": "A practical case for local models in small firms",
        "hook": (
            "Most small teams send client files to a cloud AI without thinking about where "
            "that data ends up."
        ),
        "body": (
            "## The case\n\nLocal AI agents keep proprietary data on the machine. A "
            "9B-parameter model now runs on a consumer GPU with 12 GB of VRAM.\n\n"
            "## What to run first\n\nRoutine drafting and ticket summaries, which avoid "
            "per-token cloud fees when they run locally.\n\n## What to keep in the cloud"
            "\n\nLong, multi-step reasoning, where frontier models still lead."
        ),
        "cta": "Follow for the full local-AI checklist series",
        "lead_magnet": "A one-page local-AI readiness checklist.",
        "cited_claims": [1, 2, 3, 4],
        "hook_type": "statement",
        "cta_type": "subscribe",
    }
]
RUMBLE_VIDEOS = [
    {
        "title_candidates": ["Keep Client Data Off the Cloud", "Local AI in 60 Seconds"],
        "title": "Local AI in 60 Seconds",
        "hook": "Your client files never have to leave the office.",
        "sections": [
            {"heading": "Beat 1", "text": "Local AI agents keep proprietary data on the machine."},
            {"heading": "Beat 2", "text": "A 9B-parameter model runs on a 12 GB consumer GPU."},
            {"heading": "Beat 3", "text": "Routine drafting runs locally, no per-token fees."},
        ],
        "description": (
            "A one-minute look at running routine AI work on your own hardware so client "
            "data stays on the machine."
        ),
        "thumbnail_concepts": ["Laptop inside a padlock"],
        "cta": "Follow for the full local-AI checklist series",
        "hashtags": ["LocalAI"],
        "cited_claims": [1, 2, 3],
        "hook_type": "statement",
        "cta_type": "follow",
    }
]
MASTODON_POSTS = [
    {
        "posts": [
            "Small firms do not have to send client files to a cloud chatbot. Local AI "
            "agents keep proprietary data on the machine.",
            "A 9B-parameter model runs on a consumer GPU with 12 GB of VRAM. That covers "
            "drafting and ticket summaries without per-token fees.",
            "Long, multi-step reasoning is still where frontier models win. Use both on purpose.",
        ],
        "content_warning": None,
        "cta": "Follow for the full local-AI checklist series",
        "hashtags": ["LocalAI", "SmallBusiness"],
        "cited_claims": [1, 2, 3, 4],
        "hook_type": "statement",
        "cta_type": "follow",
    }
]


# ------------------------------------------------------------------ Phase 7 (§55 E2E)
# The §55 campaign adds YOUTUBE and INSTAGRAM; the planner answer for it (selected by the
# platform list in the prompt) includes items for both. Until Phase 10 they were reported as
# skipped (decision D2); since Phase 10 their writers produce them.
E2E_PLAN = {
    "items": [
        plan_item(
            1,
            "I1",
            "LINKEDIN",
            "TEXT_POST",
            "TACTICAL",
            "AUTHORITY",
            "AWARENESS",
            "Follow for the full local-AI checklist series",
            8,
            3,
        ),
        plan_item(
            2,
            "I2",
            "X",
            "THREAD",
            "CONTRARIAN",
            "REACH",
            "AWARENESS",
            "Repost if your team pays per token",
            12,
            2,
        ),
        plan_item(
            3,
            "I3",
            "YOUTUBE",
            "LONG_VIDEO_SCRIPT",
            "CASE_STUDY",
            "AUTHORITY",
            "CONSIDERATION",
            None,
            15,
            4,
        ),
        plan_item(
            4,
            "I4",
            "LINKEDIN",
            "CAROUSEL",
            "COMPARISON",
            "AUTHORITY",
            "CONSIDERATION",
            CALL,
            9,
            3,
            depends_on=1,
        ),
        plan_item(
            5, "I5", "INSTAGRAM", "CAROUSEL", "EDUCATIONAL", "FOLLOWERS", "AWARENESS", None, 18, 3
        ),
        plan_item(6, "I6", "X", "TEXT_POST", "DATA_DRIVEN", "REACH", "AWARENESS", None, 11, 3),
        plan_item(
            7,
            "I7",
            "LINKEDIN",
            "TEXT_POST",
            "STORY",
            "LEADS",
            "CONVERSION",
            CALL,
            9,
            3,
            depends_on=4,
        ),
    ]
}
OPTIMIZER = {
    "recommendations": [
        {
            "statement": "Publish more tactical LinkedIn text posts with a clear follow CTA.",
            "rationale": (
                "S1 has the highest engagement rate in the report; the sample is small "
                "(one post), so treat this as a direction to test, not a conclusion."
            ),
            "target": "LINKEDIN format=TEXT_POST",
            "evidence": ["S1", "S2"],
        },
        {
            "statement": "Keep X threads short and contrarian; they drove clicks.",
            "rationale": "S3 shows the best click rate among the published posts.",
            "target": "X format=THREAD",
            "evidence": ["S3"],
        },
        {
            # Cites a row that does not exist: must be dropped, never stored.
            "statement": "Double the posting frequency on Instagram.",
            "rationale": "Instagram engagement is high (row S9).",
            "target": "INSTAGRAM",
            "evidence": ["S9"],
        },
    ]
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write("campaign__research.json", [RESEARCH])
    write("campaign__strategy.json", [STRATEGY])
    complete = ideas()
    # First answer skips CASE_STUDY: the schema rejects it and the repair loop runs once.
    incomplete = [i for i in complete if i["angle"] != "CASE_STUDY"]
    write("campaign__opportunity.json", [{"ideas": incomplete}, {"ideas": complete}])
    write(
        "campaign__planner.json",
        [PLAN, {"match": "YOUTUBE; INSTAGRAM", "response": E2E_PLAN}],
    )
    write("content__linkedin_writer.json", LINKEDIN_POSTS)
    write("content__x_writer.json", X_POSTS)
    write("content__youtube_writer.json", YOUTUBE_VIDEOS)
    write("content__instagram_writer.json", INSTAGRAM_POSTS)
    write("content__facebook_writer.json", FACEBOOK_POSTS)
    write("content__reddit_writer.json", REDDIT_POSTS)
    write("content__substack_writer.json", SUBSTACK_POSTS)
    write("content__rumble_writer.json", RUMBLE_VIDEOS)
    write("content__mastodon_writer.json", MASTODON_POSTS)
    write("analytics__optimizer.json", [OPTIMIZER])


if __name__ == "__main__":
    main()
