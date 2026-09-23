# ENGINEERING_LOG.md

Append-only. One entry per task and per phase gate (A11). Newest at bottom.

---

## 2026-09-22 — Bootstrap
- Created `C:\dev\omnigrowth` as a fresh project folder (user instruction: "start from scratch, separate project folder").
- Copied governance pack from `Downloads\omnigrowth-hermes\omnigrowth-hermes\` (AGENTS.md, docs/SPEC_AMENDMENTS.md, docs/PHASES.md, prompts/, skills/) unchanged.
- Saved the user-supplied requirements verbatim as `docs/spec/OMNIGROWTH_SPEC.md` (previously missing; that was the preflight blocker).
- Set `Working directory` in `prompts/01_KICKOFF.md` to `C:/dev/omnigrowth`.
- Preflight: all four binding docs present; skills `omnigrowth-phase-gate` and `omnigrowth-debug-loop` loadable.

## 2026-09-22 — Phase 0 gate

```
PHASE 0 — Discovery — GATE_PASSED
COMPLETED:
  - docs/audit/probe.ps1 (single batched probe), run once
  - docs/audit/probe_output.txt (raw output, 213 lines)
  - docs/audit/PHASE0_AUDIT.md (SYSTEM · REPOSITORY · SERVICES · LOCAL LLM STATUS · DEPENDENCIES · RISKS; every claim cites L<n>)
  - docs/STATUS.md, ENGINEERING_LOG.md
TESTS: N/A (no code in Phase 0). Gate check = audit file exists + writes confined to docs/audit/, STATUS.md, ENGINEERING_LOG.md (repo not yet a git repo, so verified by file inventory L201–L210).
  probe_output.txt tail:
    [port 5173] free
    ### ENV_VARS
    [relevant_env_names] ANTHROPIC_BASE_URL / LMSTUDIO_USE_CUDA / OLLAMA_HOST / OLLAMA_NUM_THREADS / OLLAMA_USE_GPU / OPENAI_API_KEY
    ### GIT_STATE
    [git rev-parse] fatal: not a git repository (or any of the parent directories): .git
    [git config user.name] RoBoT_DinG_dONg
    ### END
    probe complete
FILES CHANGED: docs/audit/probe.ps1, docs/audit/probe_output.txt, docs/audit/PHASE0_AUDIT.md, docs/STATUS.md, ENGINEERING_LOG.md
KNOWN ISSUES: R1 host ports 5432/6379 occupied; R2 OPENAI_API_KEY in env; R3 Ollama *-cloud model; R4 GPU 9.9/12.2 GB used; R5 ANTHROPIC_BASE_URL in env; R6 NTFS bind mounts.
DECISIONS NEEDED: none blocking; R1–R3 mitigations proposed as ADRs in Phase 1.
NEXT PHASE: 1 — Architecture on paper
```

## 2026-09-22 — Phase 1 gate

```
PHASE 1 — Architecture on paper — GATE_PASSED (pending human ADR approval; commit pending signature)
COMPLETED:
  - ARCHITECTURE.md (Mermaid component diagram, layer/import rules, §55 sequence diagram, topology)
  - docs/GLOSSARY.md (canonical names; §4a = reconciliation additions)
  - docs/DATA_MODEL.md (subagent A; 35 tables incl. campaign_sources, jobs, idempotency_keys, app_settings; pgvector design)
  - docs/STATE_MACHINES.md (subagent B; Campaign, ContentAsset CA1–CA21, PublicationJob + Publication, Experiment)
  - docs/API_PLAN.md (subagent C; /api/v1 endpoints, schemas, §55 call sequence, security)
  - docs/PLATFORM_CAPABILITIES.md (subagent D; 9 platforms, all UNVERIFIED)
  - docs/adr/0001…0008 — all Status: PROPOSED
  - .gitignore; git init (branch main)
TESTS: N/A (no code in Phase 1; `uv run poe check` and `alembic heads` skipped per phase-gate skill steps 2 and 4).
  Gate checks run this session:
    git status --porcelain | grep -iE "\.env|secret|\.key|\.pem"  ->  no secrets staged
    grep -c "Status: PROPOSED" docs/adr/*.md  ->  1 in each of 0001…0008
  Reviewer pass 1: BLOCKER 5 · MAJOR 28 · MINOR 19
  Reviewer pass 2 (re-verification): items 1–33 FIXED 29, PARTIAL 4, NOT FIXED 0. New: BLOCKER 0 · MAJOR 4 (N1–N4) · MINOR 12
  Primary then fixed N1 (scheduling→publishing import rule), N2 (/content requires ACTIVE), N3 (CA3 submit promotes v1, no new version),
  N4 (one details.guard value set), and partials 7 (ARCHITECTURE partial-unique wording), 32 (D2 note in sequence diagram).
  Partials 5 and 31 are resolved by the N4 and N1 fixes. No third review pass was run.
FILES CHANGED: ARCHITECTURE.md, .gitignore, docs/GLOSSARY.md, docs/DATA_MODEL.md, docs/STATE_MACHINES.md, docs/API_PLAN.md,
  docs/PLATFORM_CAPABILITIES.md, docs/adr/0001…0008, docs/STATUS.md, ENGINEERING_LOG.md
KNOWN ISSUES:
  - About 12 MINOR wording/naming mismatches from reviewer pass 2 (not itemized by the reviewer), plus minors 38–52 from pass 1 that were
    not explicitly fixed (e.g. naming drift angle/angle_type mapped in API, OpenAPI exposure, rate-limiter fail mode).
  - Mermaid diagrams were not rendered to confirm they draw correctly; only syntax was reviewed.
  - First commit NOT made: user.gitconfig has commit.gpgsign=true with gpg.format=ssh, and ssh-keygen was waiting for a key passphrase.
    The stuck process was stopped. All files remain staged.
DECISIONS NEEDED:
  - ADR-0001…0008: accept / change (see chat summary).
  - D1: MVP gate (Phase 7) requires §55 steps 15–16, but derived analytics + Optimization Agent are Phases 8–9. Either pull a minimal
    report + recommendation path into Phase 7, or amend A7 so the MVP E2E stops at step 14.
  - D2: §55 lists YouTube/Instagram, but A7 MVP writers are LinkedIn + X. Skip those plan items as NOT_IMPLEMENTED in the MVP E2E,
    or build minimal writers early?
  - Q6 (STATE_MACHINES): Should LIVE publishing ignore auto-approval and always require a human approval?
  - Partial vs full unique idempotency index (ADR-0003).
NEXT PHASE: 2 — Skeleton + core backend (after ADR approval)
```

## 2026-09-23 — Phase 1 gate decisions (human: "proceed")
- ADR-0001…0008 → ACCEPTED. The human gave no per-option choice, so the primary agent adopted its recommended defaults and logged them:
  D1 = a minimal analytics + recommendations slice in Phase 7 (the MVP E2E covers all 16 §55 steps; PHASES.md updated);
  D2 = YouTube/Instagram plan items are `skipped: NOT_IMPLEMENTED` until Phase 10;
  Q6 = LIVE publishing requires a HUMAN approval (auto-approve is never enough for LIVE);
  the partial unique idempotency index is accepted. These can be overridden.

## 2026-09-23 — Phase 2 debug-loop entries
1. Symptom: `alembic check` reported 3 missing FKs after the first upgrade. Cause: autogenerate leaves out `use_alter=True` composite FKs (content_assets→content_versions ×2, experiments→experiment_variants). Fix: added `DEFERRED_FKS` to the initial revision (create after tables, drop first on downgrade). Verify: downgrade base → upgrade head → `alembic check`: "No new upgrade operations detected."
2. Symptom: integration tests failed with `psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`. Cause: the Windows default asyncio loop is incompatible with psycopg async (ENV/TOOLING). Fix: `omnigrowth/core/aio.py` selector loop factory, used by pytest (`pytest_asyncio_loop_factories` hook), the scheduler (`aio.run`) and `poe api` (`--loop omnigrowth.core.aio:loop_factory`). Verify: 8/8 integration tests pass; the native API smoke test returned HTTP 200.
3. Symptom: `tmp_path` fixture failed with `PermissionError [WinError 5]` on the user temp dir (sandbox). Fix: `addopts = --basetemp=.pytest-tmp` (git-ignored). Verify: fixture tests pass.

```
PHASE 2 — Skeleton + core backend — GATE_PASSED (commit pending signature)
COMPLETED:
  - pyproject.toml (uv, Python >=3.12, poe tasks, ruff/mypy strict/pytest/bandit/import-linter config), uv.lock
  - omnigrowth/core: settings (explicit DATABASE_URL/REDIS_URL, LIVE forbidden under ENV=test, OMNIGROWTH_* keys only),
    errors (ErrorCode + HTTP map), enums (GLOSSARY), JSON logging + contextvars correlation, secret masking (text/mapping/
    tracebacks, uvicorn/rq loggers adopted), UUIDv7 ids, selector-loop helper
  - core/state_machines: data-driven Campaign/ContentAsset/PublicationJob/Publication/Experiment/Job machines;
    apply_transition (compare-and-set UPDATE + audit_events row, CONFLICT on lost race) — the only status writer
  - omnigrowth/models: 35 tables per DATA_MODEL.md (subagent), incl. partial/GIN/INCLUDE indexes, composite FKs, pgvector
  - alembic: initial revision (extensions vector+citext, append-only triggers on audit_events/approval_actions/
    content_versions, immutable content lineage trigger, deferred FKs, enum drops on downgrade)
  - api: app factory, correlation-id middleware, ErrorResponse handlers, GET /api/v1/health (public contract: component
    states only) (subagent) + services/health (DB/Redis/LLM probes; LLM down = DEGRADED; docker/host preset URLs)
  - scheduler process (FOUNDATION: bounded DB ping + heartbeat; the outbox claimer arrives in Phase 7), RQ worker entrypoint
  - docker-compose.yml (postgres pgvector:pg16, redis, migrate, api, worker, scheduler; 127.0.0.1-only ports; healthchecks),
    Dockerfile (non-root), .dockerignore, .env.example, .gitattributes, .github/workflows/ci.yml (3.12+3.13 check, integration, security)
TESTS (all run in this session, 2026-09-23):
  uv run poe check
    Poe => ruff format --check omnigrowth tests alembic   -> 71 files already formatted
    Poe => ruff check omnigrowth tests alembic            -> All checks passed!
    Poe => mypy omnigrowth                                -> Success: no issues found in 56 source files
    Poe => lint-imports                                   -> Contracts: 3 kept, 0 broken.
    Poe => pytest -m 'not live_llm and not integration'   -> 221 passed, 8 deselected in 1.59s
  uv run pytest -m integration -q                         -> 8 passed, 221 deselected in 7.39s
      (fresh empty DB -> alembic upgrade head, single head, alembic check clean, extensions, apply_transition + audit,
       CAS conflict, append-only trigger, enum rejection, downgrade base -> upgrade head)
  uv run alembic heads                                    -> 83579cc9b1d2 (head)
  docker compose up -d --build --wait                     -> api, postgres, redis, worker, scheduler Healthy; migrate Exited (0)
  curl http://127.0.0.1:8000/api/v1/health                -> HTTP/1.1 200 OK
      {"status":"OK","time":"2026-09-23T20:33:33.660637Z","checks":{"database":"OK","redis":"OK","local_llm":"OK"}}
  secret-masking tests (tests/unit/test_core.py)          -> included in the 221 passed
  uv run poe security                                     -> bandit clean (2 enum-label false positives marked nosec); pip-audit: No known vulnerabilities found
  Native Windows smoke tests: poe api on :8011 -> HTTP 200; `python -m omnigrowth.workflows.worker --burst ingest` -> started and quit cleanly
  Skipped: none. live_llm: none exist yet (Phase 3).
REVIEWER (gate): BLOCKER 0 · MAJOR 7 · MINOR 15. All 7 MAJOR fixed and re-verified by the runs above:
  #1 poe worker had no REDIS_URL -> python entrypoint using Settings; #2 plain-text tracebacks unmasked -> MaskingTextFormatter;
  #3 uvicorn loggers bypassed JSON/masking -> adopted/silenced; #4 /health leaked version/env/detail -> public contract per API_PLAN §2.1;
  #5 no doc-vs-code transition test -> test_state_machine_spec.py (63 rows parsed, exhaustive matrix, per-row actors; mutation check done);
  #6 masking gaps (redis :pw@, Token scheme, quoted values) -> fixed + tests; #7 Docker ignored LLM preset -> OMNIGROWTH_RUNTIME + base_url_docker.
  MINOR fixed: #8 EmbeddingOwnerType native enum; #9 status-write detector (setattr, dict literals, raw SQL); #14 worker/scheduler
  healthchecks + wait for migrate; #15 worker URL from settings; #16 scheduler ping timeout + tests; #17 invalid REDIS_URL -> DOWN;
  #18 non-string log extras masked; #20 .gitattributes; #21 .env.example complete.
FILES CHANGED: 112 staged (see `git status`); new trees: omnigrowth/, tests/, alembic/, config/, .github/
KNOWN ISSUES (MINOR, carried forward):
  - #10 The app connects as the Postgres superuser; no restricted `omnigrowth_app` role yet (a superuser can disable the append-only triggers). Target: Phase 4 (auth/security).
  - #11 ARCHITECTURE §3 deviations: core/state_machines imports sqlalchemy; api/app.py imports storage (engine dispose); services/health does
    LLM HTTP (rule 4). Import-linter has only 3 contracts. Target: amend ARCHITECTURE §3 or move code in Phase 3 (provider layer).
  - #12 Integration tests skip (instead of failing) when no DB is configured — CI must set REQUIRE_INTEGRATION.
  - #13 CI has no docker build job and no windows-latest job.
  - #19 No dev hot-reload compose profile / scripts/*.ps1+*.sh pair yet; NTFS bind-mount reload latency not measured (A10/ADR-0008).
  - #22 env_file .env passes POSTGRES_PASSWORD/KEK to every app container.
  - API_PLAN deltas: correlation-id format is 8–128 [A-Za-z0-9._-] (API_PLAN says 1–128 incl. ':'); /health has no per-IP rate limit
    (rate limiting arrives with auth); OpenAPI exposure toggle not implemented.
  - Commits: none yet (signing passphrase). Phase gates are recorded here, but no commit SHAs exist.
DECISIONS NEEDED: none blocking Phase 3. The user should make the first signed commit (see chat).
NEXT PHASE: 3 — Model layer
```
