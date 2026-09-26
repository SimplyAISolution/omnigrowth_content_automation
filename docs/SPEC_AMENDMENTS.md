# SPEC_AMENDMENTS.md

Overrides and resolutions to `docs/spec/OMNIGROWTH_SPEC.md`. Where an amendment cites a section (§N), it replaces or narrows that section. Anything not amended stands as written.

---

## A1 — Local model provider (amends §3, §23)
- Implement ONE provider class: `OpenAICompatibleProvider(base_url, api_key?, capabilities)`.
- Ship two presets in `config/models.yaml`:

| Preset     | Default base_url (host)          | From inside Docker                        |
|------------|----------------------------------|-------------------------------------------|
| `lmstudio` | `http://127.0.0.1:1234/v1`       | `http://host.docker.internal:1234/v1`     |
| `ollama`   | `http://127.0.0.1:11434/v1`      | `http://host.docker.internal:11434/v1`    |

- Base URL resolution: env `OMNIGROWTH_LOCAL_LLM_URL` > preset > error. Never guess.
- Structured output: probe capability at health-check time (`response_format: json_schema` → `json_object` → prompt-only). Always validate with Pydantic regardless of mode.
- Cloud providers: implement the interface + one adapter (Anthropic or OpenAI, config-selected). Disabled unless a key is present.

## A2 — Local GPU concurrency (new)
- Target host has a single 12 GB-class consumer GPU. Default `local.max_concurrency: 1` per provider. A second in-flight local request queues; it does not fail.
- Router must expose `LOCAL_ONLY | BALANCED | QUALITY_FIRST` (§51). `LOCAL_ONLY` is the default and the only profile used by tests.

## A3 — Queue and scheduling (resolves §14, §30)
Decision (record as ADR-0003; human may override at the Phase 1 gate):
- **Async work** (generation, research, embeddings, analytics pulls): Redis + **RQ**. Chosen over Celery for lower operational surface; revisit if chords/canvas workflows are needed.
- **Publication scheduling**: Postgres transactional outbox. `publication_jobs` is the source of truth; a scheduler loop claims due rows with `SELECT … FOR UPDATE SKIP LOCKED`. This gives job locking, missed-job recovery (overdue rows are just due rows), and survives Redis loss.
- Idempotency: each publication job carries `idempotency_key = sha256(content_version_id + platform_account_id)`; unique index. A job whose outcome is unknown moves to `PUBLICATION_UNCERTAIN` and requires reconciliation (fetch by platform ID or manual confirm), never auto-retry.

## A4 — Vector storage (amends §10, §24)
- Use **pgvector** in the same PostgreSQL instance. No separate vector DB.
- Embedding model is a router role (`embedding_local`) with dimension stored per row; changing models triggers a re-embed job, not a silent mismatch.

## A5 — Research without fabrication (amends §7B)
- MVP Research Agent has NO web access. It extracts claims from the source asset and labels each: `SOURCE_STATED | NEEDS_VERIFICATION | OPINION`.
- `ResearchProvider` interface with a `NullResearchProvider` default. A web provider (e.g., self-hosted SearXNG) is a later, opt-in phase.
- Any citation must carry `url`, `retrieved_at`, `excerpt_hash`. Accuracy QA rejects assets citing a source not present in the research packet.

## A6 — Publishing safety (amends §11, §15, §34)
- Live publishing requires ALL of: env `PUBLISH_MODE=live`, `platform_accounts.live_enabled = true`, an `APPROVED` content version, and no open `PUBLICATION_UNCERTAIN` for that account.
- A test must prove live publishing is impossible when `ENV=test`.
- Platforms without a usable publish API get a `ManualExportPublisher` (renders copy + media checklist; human marks published and pastes URL). Capability claims per platform go in `docs/PLATFORM_CAPABILITIES.md` marked VERIFIED (with doc link + date) or UNVERIFIED.

## A7 — MVP scope (narrows §7E, §8, §12)
- MVP = the §55 success scenario passing end-to-end in mock mode, API-only.
- MVP platform writers: **LinkedIn, X**. YouTube and Instagram next. All others: interface + stub raising `NotImplementedError` with a tracking note.
- Frontend (§12, §13) is deferred until after the MVP gate. When built: Vite + React + TypeScript, API-driven, no business logic in the client.
- n8n (§3) is deferred; backend exposes HMAC-verified webhooks so n8n can be added without backend changes.

## A8 — Deterministic testing (amends §33, §55)
- `FakeProvider` returns recorded fixtures keyed by `(prompt_id, prompt_version, input_hash)`. Missing fixture → test fails loudly (no fallback text).
- The §55 E2E test runs entirely on `FakeProvider` + mock publisher + fixture analytics.
- A separate `live_llm` E2E run against the local provider is required once at the MVP gate and its output recorded; it is allowed to produce lower-quality content but must pass schema validation.

## A9 — Autonomy (amends §46, §58)
- Autonomous within a phase. HARD STOP at every phase gate.
- Phase 0 is read-only. Phase 1 produces docs + ADRs, then stops for ADR approval before any application code.
- The "$10,000–$50,000" framing is removed; quality is defined solely by the acceptance criteria in `docs/PHASES.md`.

## A10 — Windows host (amends §42)
- The agent must detect in Phase 0 whether its terminal is PowerShell, cmd, or WSL bash, and record it in `docs/STATUS.md`.
- All developer commands go through `uv run poe <task>` so they are shell-agnostic. Anything that cannot be (e.g., Docker Desktop checks) gets both a `scripts/*.ps1` and `scripts/*.sh`.
- Bind-mount performance: if the repo is on NTFS and Docker Desktop uses the WSL2 backend, hot-reload may be slow. Record the observed state; do not move the repo without asking.

## A11 — Durable state for long builds (new)
- `docs/STATUS.md`: machine-readable header (phase, task, gate status, last green commit) + short human notes. Updated at every task boundary.
- `ENGINEERING_LOG.md`: append-only; one entry per task and per phase gate.
- `docs/adr/NNNN-title.md`: one decision per file, status `PROPOSED | ACCEPTED | SUPERSEDED`.

## A12 — Definition of "production ready" (clarifies §44, §59)
A component may be labeled production-ready only if: gates pass, failure tests exist for its external dependencies, it emits structured logs with correlation IDs, its config is documented, and it has been run once outside the test suite (smoke test recorded). Everything else is labeled `FOUNDATION` or `EXPERIMENTAL`.

## A13 — DOCX zip-bomb guard (amends the ingestion limits; accepted by the human 2026-09-26)
- The "10x overall expansion" rule is replaced: a DOCX is rejected if any ZIP member of 1 MiB or more
  (uncompressed) expands more than 100:1, if one member exceeds 50 MiB, if the total exceeds 100 MiB,
  or if it has more than 2000 members. Checked on the ZIP central directory before parsing
  (`omnigrowth/ingestion/limits.py`).
- Reason: ordinary text-heavy DOCX files compress 15-30x, so a 10x overall cap rejects real uploads,
  while the per-member ratio plus absolute caps still stop decompression bombs.
