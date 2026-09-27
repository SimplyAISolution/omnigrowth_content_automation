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

## A14 — Experiment statistics (amends §18, STATE_MACHINES §4; human defaults 2026-09-26, revised after the Phase 9 review)
- **Unit.** One published post: a content asset on one of the design's platform accounts. Its value is the primary metric, read from ONE snapshot captured closest to `observation_hours` after the post's first publication, within `± age_tolerance_hours`. The tolerance may be at most observation/4, so ages are equivalent. Impressions and repeated pulls are never extra samples. Posts are blocked by account. Only accounts where every variant has ≥ 2 measured posts are analysed, so the n that is checked, the n that is tested and the evidence ids are always the same posts.
- **Real vs simulated.** Evidence is REAL only if the snapshot source is API or MANUAL **and** the post actually went out (publish mode LIVE or MANUAL). A MOCK publication never reached an audience, so its numbers are simulated whatever their label. The ingest API refuses MANUAL metrics on a MOCK publication.
- **Sample floor.** At least 30 independent units **per variant**. `min_sample_size` counts posts per variant.
- **Power.** The target is 80%. At start, n per variant = ⌈2(z₁₋α/(2m) + z_power)² σ² / δ²⌉, where:
  - σ is the SD of the metric per post at the design age, from ≥ 10 REAL posts on the same accounts that were published in the 180 days **before the experiment was created**, excluding the experiment's own units;
  - δ is the pre-registered minimum meaningful effect;
  - m is the number of treatments.

  n is then multiplied by a pre-registered `power_inflation` (default 1.25) for within-account dependence. The requirement is max(inflated n, 30). Without a baseline, the plan says `FLOOR_ONLY_NO_BASELINE`. The plan is frozen at E2.
  - Verified by `scripts/simulate_experiment_power.py`: 1.25 met 80% power in all six simulated dependence scenarios; without it, power fell to 0.69 under negative serial correlation.
  - `POSTING_TIME` experiments are refused until a design keeps every variant's slots inside the allocation period.
- **Confidence and method.** Fixed at 0.95; it is not configurable per experiment.
  - **Decision: a design-based randomization test.** The actual blocked allocation is re-drawn 9,999 times within each account's runs, and the statistic is the account-stratified difference in means (weights n_c·n_t/(n_c+n_t)). It is valid **whatever the dependence between posts of the same account**. The interval comes from inverting the same test (constant-shift model).
  - With m treatments, the decision uses p ≤ α/m and the interval is at level 1 − α/m (Bonferroni), so the decision and the interval always agree.
  - Welch statistics are reported as descriptive only, because they assume independence.
  - The power plan (normal approximation) was verified by simulation against this test under within-account dependence (docs/EXPERIMENTS.md §4).
  - `confidence` stores the level (1 − α); it is never described as the probability that a variant is better.
- **Several significant treatments.** If more than one treatment beats the control, the best one wins only if it is also significantly better than each of the others in pairwise randomization tests (Bonferroni over those pairs). Otherwise the verdict is `TREATMENTS_BEAT_CONTROL_UNRANKED`, with no winner.
- **Pre-registration.** The following are fixed at E1 and frozen at E2: primary metric, direction, allocation, observation window, stopping rule, platform, accounts, and variant order (the first is the control).
  - Allocation is blocked by account, with random block sizes of V or 2V and seeded order, so it is re-derivable.
  - The stopping rule is `FIXED_ENDPOINT`: one evaluation after `end_at`, never before, never repeated.
  - **Timeline**, fixed at E1 and checked at E2:
    - `allocation_end_at` is the last registration or publication.
    - `collection_cutoff_at` = `allocation_end_at` + observation + tolerance + `analytics_lag_hours`.
    - `end_at` = the evaluation time = `collection_cutoff_at`.
    - Start guards: pulls must fall inside every measurement window (2 × tolerance ≥ pull interval), and the lag must be ≥ the pull interval.
    - Posts published after `allocation_end_at` (or, for randomized designs, before the start) are excluded and labelled. A post published at the last allowed moment is fully measured.
  - A post can be a unit of only one active experiment.
- **Verdicts.**
  - `WINNER` (CONCLUDED) requires a randomized design, REAL data, and every variant ≥ the frozen requirement.
  - `INSUFFICIENT_EVIDENCE`, `NO_DETECTABLE_DIFFERENCE`, `TREATMENTS_BEAT_CONTROL_UNRANKED`, `NO_REAL_DATA` and `OBSERVATIONAL_ASSOCIATION` are all INCONCLUSIVE, with no winner.
  - Effect sizes (difference, relative lift against the block-weighted control mean, Hedges' g), intervals and n are always reported.
- **Observational.** Nonrandomized experiments are always labelled observational. The database forbids a winner on them.
- **Simulated evidence.** Analysed separately (`result.simulated`) and never used for the verdict. Nothing changes production strategy automatically.
