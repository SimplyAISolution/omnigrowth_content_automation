# Follow-up plan after the MVP milestone

Status on 2026-09-25: MVP gate passed (Phase 7, commit `4c23159`), then the known issues listed
in ENGINEERING_LOG ("Post-MVP known-issue pass") were resolved. Everything below is either
the next roadmap phase (docs/PHASES.md) or an item deliberately left for the phase that owns it.
Each step lists what it needs from a human, if anything.

## Next up (in order)

### 1. Phase 8 — Analytics depth
- Derived-metric definitions as data: `derived_metric_definitions` API (restricted formula AST,
  never `eval`), configurable weights, `content_efficiency` (§17), report cites the revision used.
- Attribute comparisons (§16): `GET /analytics/attribute-comparisons` grouped by topic, pillar,
  hook_type, platform, format, word-count bucket, cta_type, posting hour, model, prompt version;
  `n`, mean, median and an honest `sufficient` flag against `min_sample`.
- `GET /analytics/publications/{id}/metrics`, calendar view (`GET /calendar`).
- Scheduled `ANALYTICS_PULL` for recently published posts (scheduler), X9: stop polling DELETED.
- Gate: hand-computed metric fixtures; nullable metrics never yield divide-by-zero or fake zeros.
- Human: none.

### 2. Phase 9 — Experiments + optimization
- Experiment framework (§18): variants differing in one declared variable, min sample and
  confidence frozen at start, E-machine transitions, "no winner below threshold" test.
- Optimization Agent consumes attribute comparisons (not only the campaign report); its
  recommendations can target experiments. Recommendation → accepted → drives the next
  campaign's planning brief.
- Human: pick default `min_sample_size` / `confidence_threshold` (proposed: 30 / 0.95).

### 3. Phase 10 — Breadth: platforms + repurposing
- YouTube and Instagram writers first (removes the D2 `skipped: NOT_IMPLEMENTED` items from the
  §55 flow), then Facebook, Reddit, Substack, Rumble, Mastodon; QA rules per platform in
  `config/qa.yaml` (limits stay UNVERIFIED until Phase 13).
- Repurposing graph (§9) with cycle prevention; `POST /content/{id}/repurpose` (today 501).
- Human: none (writers only draft; nothing is published).

### 4. Phase 11 — Frontend (Vite + React + TS)
- Approvals queue first (it drives the human-in-the-loop), then Content Queue, Calendar,
  Campaigns, Analytics. API-only today.
- Human: design preferences, if any.

### 5. Phase 12 — n8n integration
- HMAC-verified inbound webhooks (`WEBHOOK_ENDPOINT` principals for Idempotency-Key),
  outbound event notifications, example workflows in `n8n/`.

### 6. Phase 13 — Real connectors (one platform per session)
- Verify API capability from official docs → PLATFORM_CAPABILITIES.md VERIFIED → adapter behind
  the A6 live guard → credentials (encrypted, `OMNIGROWTH_CREDENTIAL_KEK`) → first live post run
  by the human.
- Unblocks the items below that need a real platform.
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

## Decisions waiting on a human
- ADR-0003 partial unique index wording (PROPOSED, used since Phase 1) — confirm.
- DOCX zip-bomb ratio 100:1 per member instead of 10x overall — accept as a SPEC_AMENDMENT?
- One local LLM server (LM Studio) vs both LM Studio + Ollama on the 12 GB GPU.
- Writers on `REASONING_LOCAL` (same model as planning, no GPU swaps) vs `FRONTIER_CREATIVE`.
