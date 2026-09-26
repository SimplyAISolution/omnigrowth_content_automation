```
phase: 8
task: phase-8 gate (analytics depth + safeguards)
gate: GATE_PASSED
last_green_commit: d3db1ec
next_phase: 9 — Experiments + optimization
shell: PowerShell Core 7.6.6 (Git Bash also available)
blockers: phase 8 commit pending (signing)
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
