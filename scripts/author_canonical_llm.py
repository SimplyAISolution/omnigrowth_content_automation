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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write("campaign__research.json", [RESEARCH])
    write("campaign__strategy.json", [STRATEGY])
    complete = ideas()
    # First answer skips CASE_STUDY: the schema rejects it and the repair loop runs once.
    incomplete = [i for i in complete if i["angle"] != "CASE_STUDY"]
    write("campaign__opportunity.json", [{"ideas": incomplete}, {"ideas": complete}])
    write("campaign__planner.json", [PLAN])


if __name__ == "__main__":
    main()
