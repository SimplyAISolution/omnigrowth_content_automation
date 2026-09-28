# Follow-up plan after the MVP milestone

Status on 2026-09-25: MVP gate passed (Phase 7, commit `4c23159`), then the known issues listed
in ENGINEERING_LOG ("Post-MVP known-issue pass") were resolved. Everything below is either
the next roadmap phase (docs/PHASES.md) or an item deliberately left for the phase that owns it.
Each step lists what it needs from a human, if anything.

## Next up (in order)

### 1. Phase 8 — Analytics depth — DONE (2026-09-26, see ENGINEERING_LOG, docs/METRICS.md)

Left open from Phase 8:
- `POST /calendar/items/{id}/reschedule` and `/calendar/plan-items/{id}/move`. Rescheduling works today through `/approvals/{id}/actions` (CA12). Plan-item move is not implemented.
- Metric normalisation by post age is not done. Reports use the latest snapshot as-is (METRICS.md §5).
- ~~The inference slot TTL is not renewed, and the backlog cap is best-effort.~~ Fixed in the Phase 9 pre-gate: a renewable lease with self-fencing, and an advisory-locked cap (safeguards_post_mvp.md §2–§3).

### 2. Phase 9 — Experiments — DONE (2026-09-26; see docs/EXPERIMENTS.md, SPEC_AMENDMENTS A14)

Left open from Phase 9, deliberately:
- **Accepted recommendations feeding the next campaign's planning brief.** Not done: the human asked that nothing change production strategy automatically. A future feed must exclude simulated evidence (`evidence_query.simulated_evidence`) and stay opt-in per campaign.
- **Optimization Agent consuming attribute comparisons and CONCLUDED experiments.** It still reads the campaign report only.
- **`PATCH /experiments` and the variant PATCH/POST from API_PLAN §2.11.** Not implemented, because designs are fixed at creation.
- **Real experiment data.** It needs real posts (MANUAL export or Phase 13 LIVE). Until then, every mock-published experiment ends `NO_REAL_DATA`.

### 3. Phase 10 — Breadth: platforms + repurposing — DONE (2026-09-27; SPEC_AMENDMENTS A15)
Left open:
- Formats not written yet: `COMMENT_DRAFT` (all), Facebook `REEL_SCRIPT`/`STORY`/`LONG_VIDEO_OUTLINE`,
  Mastodon `IMAGE_POST`/`POLL`. Facebook/Rumble/Mastodon format lists await human confirmation.
- Platform limits in `config/qa.yaml` stay UNVERIFIED until Phase 13.
- Only the YouTube and Instagram writers ran against the live model; the other five are covered
  by fixtures, unit and integration tests.
- Repurposing always derives from the source's current version; choosing an older version and a
  "repurpose tree" view (all descendants) are not built.
- The planner prompt still lists formats by example; unsupported formats are corrected by
  `planner.schedule` from `config/qa.yaml`.

### 4. Phase 11 — Frontend (Vite + React + TS) — DONE (2026-09-27; frontend/README.md)
Left open (each is explained in the UI where the action would be):
- No API yet for: pausing, duplicating or archiving campaigns; archiving content; users and
  settings; model/routing views; retrying a FAILED publication job (schedule again instead).
- Not built in the UI although the API supports it: designing experiments, editing the brand
  profile/audiences, file and URL source upload, editing platform settings (subreddit
  profiles, instance limits), editing production fields (`platform_fields`), manual metric entry.
- The approval-action menu offered per state and the "real evidence" rule on the analytics
  screen are presentation copies of backend rules (the API still decides); exposing
  `is_real_evidence` per publication from the API would remove the copy.
- `/content` returns at most 200 items (no pagination in the UI yet).
- ~~The frontend gate is separate from `poe check`.~~ Decided 2026-09-27 (Phase 12 kickoff):
  `poe ui-check` is part of `poe check` (AGENTS.md §4); `poe ui-e2e` stays separate and is
  required at gates that change frontend journeys, auth, authorization or UI API contracts.
- Publication screens follow the server's publish mode; only MOCK exists today.

### 5. ~~Phase 12 — n8n integration~~ Done 2026-09-27 (A16, `n8n/README.md`). Left open:
- `publish_mode` is null on publication jobs that fail before dispatch (events say "unknown").
- The P8 manual "not published" outcome emits no event.
- The example workflow acknowledges before its notify step; a failing notify step is not
  retried by OmniGrowth (n8n's execution log shows it).
- `analytics.snapshots` inbound is not offered (would let an integration inject evidence);
  needs a decision before it is added.
- One subscription secret per workflow; the workflow's duplicate filter lives in its static data.

### 6. Phase 13 — Real connectors (one platform per session)
- **Mastodon, agent part done 2026-09-27** (A17, docs/connectors/mastodon.md). Waiting for the
  human: account + server, token, `ENABLE LIVE PUBLISHING FOR MASTODON`, first live post, then
  one metrics pull compared with PLATFORM_CAPABILITIES §4.9.
- Left open:
  - Text only: no media upload (UNVERIFIED), polls, content warnings or delete action.
  - No "verify token" action: the token's real grants are first exercised by the first live
    dispatch or lookup. Required scopes are checked as declared.
  - Instance limits come from account settings. They are not fetched from the server.
  - P8 (manual export not posted) has its event trigger but no service or UI. No
    `ManualExportPublisher` exists yet.
  - Only tested against a local fake server. No real Mastodon or real n8n instance was used;
    the n8n workflow passed offline schema validation.
  - Pre-existing flaky test: `test_phase9_experiments.py::test_two_treatments_beating_control_need_a_pairwise_difference`
    draws a random allocation seed (`secrets.randbits`), so this borderline case occasionally
    yields WINNER (seen once in 4 full runs on 2026-09-27; it passed on the rerun and 7 times
    in isolation). Pin the seed in the test.
- Next platforms need their own session, docs verification and human decisions. X costs money
  (pay-per-use); LinkedIn needs a developer app product.
- Human: API access and credentials per platform; the phrase
  `ENABLE LIVE PUBLISHING FOR <platform>` before any live post (AGENTS.md).

### 7. Phase 14 — Acceptance review (§59)
- Independent full review against the §59 checklist; final engineering report.

## Deferred on purpose (owned by a later phase)

| Item | Why it waits | Owner |
|---|---|---|
| Automatic reconciliation of `PUBLICATION_UNCERTAIN` (J12 by lookup) | Needs a platform lookup API; the mock cannot prove anything across processes. Humans reconcile via `/publications/{id}/reconcile` today. | Phase 13 |
| `ManualExportPublisher` (`PUBLISH_MODE=MANUAL`), mark-published / not-published, manual metrics entry | Only needed for platforms without a usable publish API; decided per platform after verification. | Phase 13 |
| Auto-approve must never be sufficient for LIVE (A6) | LIVE is refused outright today; the guard lands with the first live adapter. | Phase 13 |
| LLM-based brand/tone QA (Stage 2 is rule-based) | Needs an evaluation set to avoid noisy failures. | Phase 9 (with experiments) |
| Per-model HNSW index for `embeddings` | Brute-force cosine is fine at MVP volumes. | When >50k vectors per brand |
| `/replan`, strategy PATCH/regenerate, 14/30-day plans, BUDGET_EXCEEDED pre-flight, `ResearchProvider` web provider | Product features beyond the MVP scenario. | Phase 8-9 or on demand |
| Rate limits beyond login + ingest | No public exposure yet (API bound to 127.0.0.1). | Before any remote deployment |
| Superuser DB role, CI docker/windows jobs, dev hot-reload profile | Operational hardening. | Before any remote deployment |

## Decisions (resolved 2026-09-26)
- 2026-09-27 (Phase 11 kickoff): keep the Phase 10 format subsets as the current supported scope, not verified capabilities — Facebook TEXT_POST/IMAGE_POST; Rumble LONG_VIDEO_OUTLINE/LONG_VIDEO_SCRIPT/SHORT; Mastodon TEXT_POST/THREAD.
- ADR-0003 was already ACCEPTED at the Phase 1 gate; nothing to confirm.
- DOCX zip-bomb guard: 100:1 per member accepted -> SPEC_AMENDMENTS A13.
- Local LLM server: LM Studio only; Ollama removed from role routing (config/models.yaml).
- Writers stay on REASONING_LOCAL (same model as planning, no GPU swaps).
