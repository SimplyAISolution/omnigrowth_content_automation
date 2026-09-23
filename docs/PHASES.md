# PHASES.md — OmniGrowth build plan

Re-sequenced from spec §45 to reach a working vertical slice (MVP = §55 in mock mode) before breadth.
Each phase: **one Hermes session** (or a few, resumed via `docs/STATUS.md`). Hard stop at every gate.

Legend — **P** = primary agent only · **D** = safe to `delegate_task` (disjoint files) · **R** = reviewer subagent at gate

---

## Phase 0 — Discovery (READ-ONLY)
**Goal:** know the machine before touching it.
- Detect: OS build, shell type, Python/uv/Node/Docker/WSL versions, Docker Desktop running, free disk, GPU (`nvidia-smi`), git state of repo dir.
- Probe local LLM endpoints from A1 (`GET /v1/models`) on host. Record which respond and which models are loaded.
- Inventory any existing files in the repo dir. Do not modify.
**Deliverable:** `docs/audit/PHASE0_AUDIT.md` with sections SYSTEM · REPOSITORY · SERVICES · LOCAL LLM STATUS · DEPENDENCIES · RISKS.
**Gate:** file exists; zero files modified outside `docs/audit/` (`git status` shows only that path, or repo not yet initialized).
**Stop.**

## Phase 1 — Architecture on paper
**Goal:** decisions made cheaply, before code.
- `ARCHITECTURE.md`: component diagram (Mermaid), layer boundaries, data flow for §55.
- `docs/DATA_MODEL.md`: ERD (Mermaid) covering §31 tables; indexes with justification.
- `docs/STATE_MACHINES.md`: Campaign, ContentAsset, Publication, Experiment — states, transitions, guards, who may trigger.
- `docs/API_PLAN.md`: §32 endpoints, request/response schema names, auth requirement.
- `docs/adr/0001…000N`: stack, provider abstraction (A1), queue/outbox (A3), pgvector (A4), task runner (uv+poe), auth approach, frontend deferral.
- `docs/PLATFORM_CAPABILITIES.md`: table per platform, all rows UNVERIFIED initially.
**Gate:** R reviews for contradictions against spec + amendments. All ADRs `PROPOSED`.
**Stop. Human approves/edits ADRs → mark ACCEPTED.**

## Phase 2 — Skeleton + core backend
- P: `pyproject.toml` (uv), poe tasks, ruff/mypy/pytest config, `.env.example`, `docker-compose.yml` (api, worker, scheduler, postgres+pgvector, redis), Dockerfiles, CI workflow (§43).
- P: `omnigrowth/core/` — settings (pydantic-settings), errors taxonomy (§29), JSON logging with correlation IDs (§28), secret masking filter.
- P: SQLAlchemy models + first Alembic revision; state-machine module + transition tests.
- D: health endpoint (`/api/v1/health` checks DB, Redis, local LLM reachability — degraded, not failed, if LLM down).
**Gate:** `uv run poe check` green · `docker compose up -d --wait` healthy · health endpoint returns JSON · `alembic upgrade head` on empty DB · one Alembic head · secret-masking test passes.

## Phase 3 — Model layer
- P: provider interface, `OpenAICompatibleProvider`, `FakeProvider`, router reading `config/models.yaml`, routing profiles, per-provider concurrency semaphore (A2), privacy tags on tasks (§52: `private` tasks never route to cloud).
- P: `generate_structured(schema, prompt_ref, inputs)` with repair loop (§35), `model_runs` persistence, token/cost accounting.
- D: prompt registry loader (versioned files, front-matter validation).
**Gate:** check green · failure tests: provider offline, timeout, malformed JSON ×3, rate-limit · `live_llm` smoke test run once against detected local endpoint, output recorded.

## Phase 4 — Brand system + ingestion
- D (parallel, disjoint): Brand Profile CRUD (§5) · ingestion parsers TXT/MD/DOCX/PDF/SRT/VTT → `NormalizedDocument`.
- P: URL ingestion with SSRF guard (§26): scheme allowlist, resolve-then-connect to pinned IP, block loopback/RFC1918/link-local/CGNAT/ULA/metadata ranges, re-validate every redirect hop, size + content-type limits, timeout.
**Gate:** check green · SSRF test matrix (≥15 cases incl. redirect-to-private, IPv6 `::ffff:127.0.0.1`, decimal-encoded IP, DNS returning private IP) · corrupted-file tests.

## Phase 5 — Campaign intelligence
- D (parallel): Strategy, Research (A5 constraints), Opportunity, Planner agents — each = prompt file + Pydantic output schema + service + FakeProvider fixtures + tests.
- P: campaign orchestration service chaining them; persists each agent run with prompt version and model.
**Gate:** check green · orchestration test: source → strategy → research packet → ≥20 ideas across the 8 angle types → 7-day plan, all schema-valid.

## Phase 6 — Generation + QA + versioning
- D (parallel): LinkedIn writer, X writer (formats per §8).
- P: `content_versions` with diffing (§36), QA pipeline stages 1–4 (§11), duplicate/fatigue detection via pgvector with configurable threshold (§10, warn-only).
**Gate:** check green · QA rejects: missing CTA, banned phrase, citation not in research packet, over-length X post · near-duplicate warning fires on fixture pair.

## Phase 7 — Approval, scheduler, mock publish, mock analytics → **MVP GATE**
- P: approval actions (§37) with actor recording; outbox scheduler (A3); mock publisher (§34); fixture analytics ingestion.
- P: **minimal analytics + recommendations slice** (decision D1, 2026-09-23): derived rates from §17 with NULL-safe math, campaign report endpoint, and an Optimization Agent run whose recommendations must cite stored `analytics_snapshots` ids. Phases 8–9 extend this (configurable weights, attribute comparisons, experiments).
- P: **§55 end-to-end test** on FakeProvider, all 16 steps. YouTube/Instagram plan items are reported as `skipped: NOT_IMPLEMENTED` until Phase 10 (decision D2).
**Gate:** check green · E2E green · duplicate-publish test (two schedulers racing) → exactly one publication · `PUBLICATION_UNCERTAIN` path tested · `live_llm` E2E run once, recorded · R full review.
**Stop. MVP milestone report.**

## Phase 8 — Analytics
- Derived metrics with configurable weights (§17), attribute-to-outcome queries (§16), campaign report endpoint.
**Gate:** metric math unit-tested with hand-computed fixtures; nullable metrics never produce divide-by-zero or fake zeros.

## Phase 9 — Experiments + optimization
- Experiment framework (§18) with minimum-sample and confidence guards; Optimization Agent (§19) whose every recommendation references stored snapshot IDs.
**Gate:** test proves no winner declared below threshold; test proves recommendation without evidence IDs is rejected.

## Phase 10 — Breadth: platforms + repurposing graph
- D (parallel): YouTube, Instagram writers; then Facebook, Reddit, Substack, Rumble, Mastodon.
- P: repurposing graph (§9) with cycle prevention.
**Gate:** cycle-creation test fails correctly; each writer has format tests.

## Phase 11 — Frontend (Vite + React + TS)
- Views from §12 in priority order: Approvals → Content Queue → Calendar → Campaigns → Analytics → rest.
**Gate:** typecheck + component tests; approval flow driven through UI against running API.

## Phase 12 — n8n integration
- HMAC-verified inbound webhooks, outbound event notifications; example workflows exported to `n8n/`.

## Phase 13 — Real connectors (one platform per session)
- Verify capabilities against official docs → update PLATFORM_CAPABILITIES.md → implement adapter behind A6 gates → human provides credentials → human runs first live post.

## Phase 14 — Acceptance review (§59)
- R runs the full §59 checklist independently; primary produces the OMNIGROWTH ENGINEERING REPORT using A12 labels.
