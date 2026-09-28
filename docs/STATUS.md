```
phase: 13
task: phase-13 real connectors (Mastodon first)
gate: BLOCKED on human-only steps — agent-side implementation and all gates green (2026-09-27;
  see ENGINEERING_LOG). PHASES.md Phase 13 ends with "human provides credentials -> human runs
  first live post"; neither has happened, so Phase 13 is NOT marked passed.
last_green_commit: 8fdf486 (phase 12, signed)
next_phase: finish 13 (human: account + token + first live post), then 14 — acceptance review
shell: PowerShell Core 7.6.6 (Git Bash also available)
blockers: human must choose the Mastodon server/account (sandbox or real), store the token,
  and type ENABLE LIVE PUBLISHING FOR MASTODON before the first live post
```

## Notes
- 2026-09-22: Repo created fresh at `C:\dev\omnigrowth` from the governance pack + user-supplied spec.
- 2026-09-23: Phase 1 ADRs 0001–0008 ACCEPTED (human "proceed"); decisions D1 (minimal analytics + recommendations in Phase 7), D2 (YouTube/Instagram NOT_IMPLEMENTED in MVP E2E), Q6 (LIVE needs HUMAN approval), partial unique idempotency index — recorded in docs.
- Phase 2: skeleton + core backend. Stack runs with `docker compose up -d --build --wait` (project `omnigrowth`, ports 8000 / 55432 / 56379 on 127.0.0.1). Local `.env` (git-ignored) holds a generated DB password.
- Windows: psycopg async needs a selector event loop → `omnigrowth.core.aio` (tests, scheduler, `poe api --loop`). RQ on Windows uses `WindowsWorker` (SimpleWorker + TimerDeathPenalty) via `poe worker`.
- Phase 3: model layer (providers, router, structured generation, prompt registry, model probe). Live smoke on LM Studio qwen/qwen3.5-9b passed; see docs/audit/phase3_*.json.
- Phase 4: auth, brand system, ingestion parsers, SSRF-guarded URL ingestion, sources API + durable jobs, body/rate limits. First OWNER: `uv run poe create-owner --email you@example.com`.
- Phase 5: planning agents (research/strategy/opportunity/planner), orchestrator, campaign API. Live planning on LM Studio qwen/qwen3.5-9b passed (~3.5 min); see docs/audit/phase5_live_planning.json.
- Phase 6: LinkedIn/X writers, content versions + diff, deterministic QA stages 1-4 (config/qa.yaml), pgvector duplicate/fatigue warnings, per-queue job timeouts. Live content run passed (20 min, 13 assets); see docs/audit/phase6_live_content.json.
- Phase 7 (MVP): approvals, outbox scheduler (SKIP LOCKED, leases, UNCERTAIN handling), MockPublisher, mock/fixture analytics + NULL-safe rates, Optimization Agent with evidence, audit timeline. §55 E2E green on fixtures and live (docs/audit/phase7_live_e2e.json).
- Post-MVP: known issues resolved (Idempotency-Key, full §37, CA12/CA17, auto-approve, QA re-run, per-model similarity thresholds, DB-clock leases, recommendation machine). Follow-up plan: docs/FOLLOW_UPS.md.
- Phase 8: derived metrics as data (formula grammar, revisions), attribute comparisons, publication metrics, calendar, scheduled pulls, simulated-vs-real provenance (docs/METRICS.md). Upload/inference safeguards hardened (docs/audit/safeguards_post_mvp.md). Live §55 green (17 min).
- Phase 9: experiments (A14: unit = post at fixed age, floor 30/variant, power 80 %, fixed 0.95, Bonferroni, fixed endpoint, simulated evidence separated) + GPU lease, backlog lock, killable parsing, context budget. Unit/integration green; live blocked by environment.
- Phase 9 closing checks: controlled live rerun, real-server cancellation/lease-loss evidence + idle gate, exact server tokenization, randomization inference + power inflation 1.25, frozen timeline. Live §55 (real local inference, mock publication) passed on a frozen tree.
- Phase 10: writers for every platform (A15; D2 retired), per-subreddit/per-instance account settings, repurposing graph (`/content/{id}/repurpose`, `/derivatives`) with DB-enforced lineage and 409 on recursive duplicates. Live §55 (real local inference, mock publication) passed with YouTube + Instagram written.
- Phase 11: Vite + React + TS frontend over /api/v1 (approvals, content, calendar, publishing, campaigns, analytics, experiments, sources, brand, platforms), generated typed client + OpenAPI drift tests, GET /platforms catalog, fixture-seeded throwaway environment for Playwright journeys (Edge). Publication shown as simulated everywhere.
- Phase 12: n8n integration. INTEGRATION principal (own actor type; HMAC; brand + operation scoped; rotate with 24 h grace, revoke, audited) for text/URL ingestion and campaign planning only. Outbound `content.approval_requested`, `publication.failed`, `publication.uncertain` via DB-trigger outbox, signed at-least-once delivery in the scheduler's own loop (bounded retries, backoff, inspectable/redeliverable failures). SSRF-guarded targets with a host:port allowlist for self-hosted n8n. Owner "Automations" page. Importable inactive workflows + setup in `n8n/`. `poe ui-check` joined `poe check`. Publication still simulated.
- Phase 13 (agent part): Mastodon live connector (verified from official docs), envelope-encrypted write-only credentials bound to the account's server, the live switch with the exact phrase, full A6 live guard incl. human approval and brand match, per-send time budget, uncertain outcomes reconciled by read-only lookup (never re-posted), SIMULATED / SANDBOX / REAL evidence everywhere, preview of exactly what will be sent, `publication.not_published_confirmed` event. Publication is still simulated until the human completes the first live post.

