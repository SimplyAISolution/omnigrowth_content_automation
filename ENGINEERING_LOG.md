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

## 2026-09-23 — Phase 3 debug-loop entries
1. Symptom: lint-imports failed "api is not allowed to import omnigrowth.providers" via api.routes.health -> services.health.
   Cause: the forbidden contract checked indirect imports, but ARCHITECTURE section 3 rule 1 forbids only direct ones. Fix: allow_indirect_imports=true on that contract. Verify: 4 kept, 0 broken.
2. Symptom: models-probe timed out on lmstudio qwen/qwen3.5-9b (180 s) and got HTTP 400 for google/gemma-4-12b-qat, although the same qwen model answered in 8-15 s minutes earlier.
   Cause (ENV): LM Studio and Ollama share one 12 GB GPU (9.6 GB in use); the preceding probe had loaded Ollama qwen3:14b. No app code patched around it.
   Mitigation within scope: providers.concurrency_group (lmstudio + ollama = local_gpu) so the app never runs two local calls at once. Test: test_providers_in_one_concurrency_group_share_a_slot.

```
PHASE 3 — Model layer — GATE_PASSED (commit pending)
COMPLETED:
  - providers/: ChatProvider protocol; OpenAICompatibleProvider (LM Studio / Ollama / optional cloud; all transport failures
    categorized; trust_env=False for local); FakeProvider (fixtures keyed prompt_id/v<version>/<input_hash>.json, missing = loud
    failure; script mode); typed models.yaml config (presets, remote_patterns, providers, roles, costs, concurrency groups);
    ModelRouter (LOCAL_ONLY/BALANCED/QUALITY_FIRST, PRIVATE never REMOTE incl. *-cloud behind Ollama, per-loop semaphores);
    factory (env > preset > not built; cloud only with OMNIGROWTH_CLOUD_API_KEY); generate_structured (json_schema ->
    json_object -> prompt-only, Pydantic validation, <=2 repairs, fallback on unavailable/timeout/rate-limit/budget,
    prompt privacy/role enforced, raw output masked + persisted); probe_structured_mode (A1 capability probe)
  - prompts/ (subagent): versioned registry, strict front-matter, safe {{ var }} rendering, deterministic input_hash;
    system/structured_probe.v1.md
  - storage/: DbRunRecorder (model_runs), sync_prompt_versions (immutable versions)
  - services/model_probe.py + `poe models-probe` (per-model reachability, locality, structured mode); health LLM check now
    goes through the provider (ARCHITECTURE section 3 rule 4); new import-linter contract "providers depend only on core"
TESTS (run this session, 2026-09-23/24):
  uv run poe check -> 91 files already formatted / All checks passed! / Success: no issues found in 68 source files /
                      Contracts: 4 kept, 0 broken. / 310 passed, 17 deselected in 4.44s
  uv run pytest -m integration -q -> 16 passed, 311 deselected in 6.38s
  Gate failure tests: provider offline, timeout, rate limit (HTTP level: test_providers.py; fallback + all-fail:
    test_structured_generation.py), malformed JSON x3 (attempts 0,1,2, raw persisted, no fallback; DB rows: test_model_runs.py)
  uv run pytest -m live_llm -> 1 passed (docs/audit/phase3_live_llm.json: lmstudio qwen/qwen3.5-9b, json_schema, 1 attempt,
    89 in / 702 out tokens, output {"topic": "Local AI agents versus cloud-hosted chatbots",
    "keywords": ["privacy","latency","deployment"]} -- schema-valid)
  uv run poe models-probe -> docs/audit/phase3_models_probe.json: ollama qwen3:8b OK json_schema; ollama qwen3:14b OK json_schema;
    both embedding models listed; lmstudio qwen/qwen3.5-9b MODEL_TIMEOUT and gemma-4-12b-qat MODEL_UNAVAILABLE under GPU
    contention (debug entry 2)
  uv run alembic heads -> 83579cc9b1d2 (head)  (no schema change this phase)
  docker compose up -d --build --wait -> all healthy; /api/v1/health {"status":"OK",...,"local_llm":"OK"}
REVIEWER: BLOCKER 0 · MAJOR 2 · MINOR 13
  Fixed MAJOR: #1 prompt privacy_class/role ignored -> enforced (stricter privacy wins, role mismatch rejected);
               #2 no capability/locality probe -> probe_structured_mode + models-probe CLI.
  Fixed MINOR: #3 detector blind spot (**splat flagged; model_runs.py allowlisted explicitly), #4 raw output masked,
               #5 unpriced remote cost = unknown (None), #7 no system proxy for local, #9 format rejections recorded,
               #10 budget checked before every remote call + falls back, #11 prompt temperature/max_tokens used (probe
               max_tokens removed), #13 per-event-loop semaphores, #15 PRIVACY_VIOLATION under LOCAL_ONLY.
KNOWN ISSUES (carried):
  - #6 a LOCAL provider pointed at a non-loopback host is still treated as LOCAL (no host-based locality check).
  - #8 capability cache is process-global and permanent (no TTL / per-schema key).
  - #12 services/health keeps its own models.yaml URL resolver (duplicates factory.resolve_base_url).
  - #14 Retry-After is captured but no same-candidate retry is attempted.
  - GPU contention: running LM Studio and Ollama models side by side on the 12 GB GPU causes timeouts; recommend pointing all
    roles at one server (the default models.yaml mixes both as fallbacks).
  - Phase 2 carry-overs: superuser DB role, CI docker/windows jobs, dev hot-reload profile, env_file breadth.
DECISIONS NEEDED: none blocking. Optional: choose ONE local LLM server for all roles (LM Studio or Ollama) to avoid GPU contention.
NEXT PHASE: 4 — Brand system + ingestion (SSRF-guarded URL ingestion)
```

## 2026-09-24 — Phase 4 debug-loop entries
1. Symptom: test_docx_upload_runs_through_ingest_job KeyError 'filename'. Cause: normalisation replaced
   source metadata recorded at accept time. Fix: merge existing metadata in process_source. Verify: 14/14.
2. Symptom (reviewer MAJOR 1): non-domain exceptions / RQ timeouts left jobs RUNNING forever. Fix: catch
   SQLAlchemyError, httpx.HTTPError, OSError, UnicodeError, ValueError, JobTimeoutException in execute_job ->
   FAILED; JOB transition JB6 `reap` + scheduler reaper (RUNNING > 15 min). Tests in test_phase4_hardening.py.

```
PHASE 4 — Brand system + ingestion — GATE_PASSED (commit pending; Phase 3 also uncommitted)
COMPLETED:
  - Auth (ADR-0006): argon2id passwords, opaque og_ bearer tokens stored as sha256 only, 12 h sessions,
    login/logout/me, role dependencies (VIEWER/EDITOR/OWNER), `poe create-owner` bootstrap CLI,
    login rate limit per IP + per email (Redis fixed window, fail closed), argon2 off the event loop
  - Brand system (subagent): brands, append-only brand_profiles versions (one active), audiences, offers;
    CRUD/archive/restore, audit rows for every change (observability/audit.py record_audit)
  - Ingestion parsers (subagent): TXT/MD/PDF/DOCX/SRT/VTT/HTML -> NormalizedDocument; magic-byte sniffing
    vs extension/declared type; limits incl. DOCX zip-bomb guard; AUDIO/VIDEO accepted -> NOT_IMPLEMENTED
  - SSRF guard (security/http.py): scheme/port/userinfo policy, obfuscated-literal rejection, resolve-once +
    all-answers-public check, connect to pinned IP (Host + SNI preserved), per-hop re-validation (max 5),
    fresh client per hop, streamed size cap, content-type allowlist, 10 s connect / 30 s total deadline
  - Sources API: /sources/text (sync, dedupe), /sources/upload + /sources/url (202 + SOURCE_INGEST job),
    list/get/content/archive/retry; durable jobs table + GET /jobs/{id}; RQ entrypoint workflows.tasks.run_job;
    content-addressed upload store; SOURCE_ASSET + JOB machines (STATE_MACHINES section 7)
  - Request-body guard middleware (1 MiB JSON / 25 MiB upload, rejects before FastAPI parses multipart)
  - Docker: shared uploads volume (api writes, worker reads), non-root-owned upload dir
TESTS (run this session, 2026-09-24):
  uv run poe check -> 133 files already formatted / All checks passed! / Success: no issues found in 101
                      source files / Contracts: 4 kept, 0 broken. / 487 passed, 55 deselected in 5.56s
  uv run pytest -m integration -q -> 54 passed, 488 deselected in 15.42s
  uv run poe security -> bandit clean; pip-audit: No known vulnerabilities found
  SSRF matrix: tests/unit/test_ssrf_guard.py 47 cases incl. redirect-to-private (IP and hostname), [::ffff:127.0.0.1],
    decimal 2130706433 (+hex/octal/short), DNS returning private IP, mixed answers, metadata IPs, NAT64/6to4,
    zone ids, pinned-IP + SNI, stalled body, total deadline
  Corrupted files: tests/unit/test_ingestion_parsers.py (83 tests incl. truncated/garbage/encrypted PDF, corrupt DOCX,
    zip bombs, NUL text, bad subtitles, type mismatches) + integration corrupted-PDF job failure
  Docker end-to-end (real API -> Redis/RQ -> worker container -> Postgres): DOCX upload 202 -> job SUCCEEDED ->
    content "Local AI agents / They keep data on your own hardware."; metadata-IP URL -> 422 SSRF_BLOCKED.
    (throwaway smoke EDITOR user + brand "phase4-smoke-*" left in the dev DB; user deactivated, tokens revoked)
  Live limits: 5x401 then 429 on login (Redis); 2 MB JSON body -> 413; /api/v1/health OK; alembic heads: 83579cc9b1d2 (head)
REVIEWER: BLOCKER 0 · MAJOR 5 · MINOR 8
  Fixed MAJOR: #1 stuck RUNNING jobs (catch + reaper), #2 raw exceptions from fetcher (wrapped; zone ids/empty labels
  rejected; UnicodeError in resolver), #3 total deadline, #4 body buffered before limits (ASGI middleware),
  #5 no rate limiting (login + ingest limits, Retry-After).
  Fixed MINOR: #6 retry for FAILED sources (SA5 + endpoint), #7 queue-down leaves orphans (job CANCELLED, source FAILED),
  #8 connection reuse across hops, #9 internal addresses hidden from job errors, #11 argon2 in thread,
  #12 LoginRequest extra=forbid, #13 STATE_MACHINES section 7 rewritten.
KNOWN ISSUES:
  - #10 API_PLAN 6.2 deltas: DOCX guard uses 100:1 per-member ratio (API_PLAN says 10x overall; 10x would reject normal
    text-heavy DOCX — keep, amendment recommended); type mismatch returns 422 not 415; upload limits are constants,
    not Settings.
  - Rate limits cover login and ingest routes only; unauthenticated health and other mutating routes have none yet.
  - Brand API deviations recorded by the subagent (POST /brands is EDITOR not OWNER; PUT /profile + version_no instead
    of PATCH + profile_revision; Page lives in schemas/common.py now).
  - The live login smoke test used the host's per-IP login budget: logins from 127.0.0.1 may return 429 for up to a minute.
  - Carried: superuser DB role, CI docker/windows jobs, dev hot-reload profile, env_file breadth, GPU contention note.
DECISIONS NEEDED: none blocking. Optional: accept the DOCX 100:1 ratio as a SPEC_AMENDMENT.
NEXT PHASE: 5 — Campaign intelligence (Strategy, Research, Opportunity, Planner agents)
```

## 2026-09-24 — Phase 5 debug-loop entries
1. Symptom: live planning run FAILED, strategy agent LLM_OUTPUT_INVALID on all 3 attempts (research needed 1 repair).
   Reproduce: `uv run pytest tests/integration/test_live_planning.py -m live_llm`. Evidence: every failed call had
   output_tokens == max_tokens (2048) while the visible content was only `{"positioning": "Trusted advisor on secure,`;
   LM Studio loaded context 70144, so the model limit was not the cause. Cause: qwen3.5-9b spends most of the output
   budget on hidden reasoning; the per-prompt max_tokens truncated the JSON. Fix: raise max_tokens (research/planner
   8192, strategy 6144, opportunity 12288; v1 files were never released, input hashes unaffected). Also added
   raw_head of failed calls to the live report. Verify: live run 1 passed in 261.31s, all agents first attempt.
2. Reviewer MAJOR #1: agent_runs left RUNNING when a job crashed or timed out (run_agent only handles domain errors).
   Fix: record_planning_failure(job_id=...) fails RUNNING agent_runs with the job's correlation id (AR3) on job failure
   and in the reaper; crash codes per job kind (planning: MODEL_TIMEOUT on RQ timeout, else INTERNAL_ERROR).
   Verify: test_crash_mid_pipeline_fails_the_running_agent_and_retry_supersedes_mix.
3. Reviewer MAJOR #2: a trivial quote ("local") kept SOURCE_STATED. Fix: excerpt must be >= 20 chars, a verbatim
   quote, and contain every number in the claim; otherwise NEEDS_VERIFICATION. Verify: unit test added.

```
PHASE 5 — Campaign intelligence — GATE_PASSED (commit pending)
COMPLETED:
  - agents/base.py run_agent: AGENT_RUN machine (AR1-AR3, new), pinned prompt_version_id (ensure_prompt_version),
    PII-free input_ref, generate_structured + DbRunRecorder, commit before/after the model call
  - Agents (prompt file + Pydantic schema + deterministic post-processing), all PRIVATE / REASONING_LOCAL:
    research (A5: no web, labels, verbatim-quote + number check, no citations), strategy (platforms clamped to the
    campaign), opportunity (>=20 ideas, every AngleType enforced in the schema -> repair loop; platforms/evidence
    cleaned), planner (7-day, relative days/hours; windows computed in the campaign timezone after the call)
  - campaigns/orchestrator.py: research -> strategy -> ideas -> deterministic mix selection (CONTENT_IDEA machine,
    new; CI2/CI4) -> plan; commit per step; C3 on success, C4 via the job runner (also crashes + reaper)
  - Campaign API: POST /campaigns, /campaigns/wizard (inline source, optional generate), GET list/detail,
    POST /{id}/generate (C2/C5, has_source guard, CAMPAIGN_PLANNING job), /{id}/activate (C7),
    GET /{id}/strategy, /research-packet, /ideas, /plan
  - FakeProvider record mode + CannedProvider; canonical answers (scripts/author_canonical_llm.py) recorded into
    tests/fixtures/llm/<prompt_id>/v1/<input_hash>.json; prompts treat user-message content as untrusted data
TESTS (run this session, 2026-09-24):
  uv run poe check -> 151 files already formatted / All checks passed! / Success: no issues found in 112 source
                      files / Contracts: 4 kept, 0 broken. / 505 passed, 61 deselected in 5.86s
  uv run poe test-int -> 59 passed, 507 deselected in 17.52s (incl. 5 planning tests: gate orchestration,
    has_source guard, model-down -> FAILED -> retry, crash mid-pipeline -> AR3 + retry supersedes mix, VIEWER 403)
  Orchestration gate (FakeProvider fixtures): source -> research packet (7 claims, 1 fabricated quote downgraded)
    -> strategy (REDDIT clamped out) -> 24 ideas across all 8 angles (first answer missing CASE_STUDY -> 1 repair)
    -> 7 selected -> 8 plan items covering days 1-7 -> READY_FOR_REVIEW -> activate -> ACTIVE
  LIVE (LM Studio qwen/qwen3.5-9b, final prompts): tests/integration/test_live_planning.py -> 1 passed in 216.94s;
    docs/audit/phase5_live_planning.json: SUCCEEDED, 25 ideas / 8 angles, 7 plan items / 7 days, 5 claims,
    every agent first attempt (research 60 s, strategy 33 s, opportunity 65 s, planner 55 s)
  alembic heads -> 83579cc9b1d2 (head) (no schema change this phase)
REVIEWER: CRITICAL 0 · MAJOR 2 · MINOR 7
  Fixed: MAJOR #1 orphaned RUNNING agent runs, MAJOR #2 trivial-quote provenance; MINOR #3 ideas list uses the
  latest SUCCEEDED run + older mix deselected (CI4 now also SYSTEM), #4 crash error codes per job kind, #5 wizard
  validates references before ingesting, #6 untrusted-data instruction + SOURCE markers, #8 plan item angle taken
  from the referenced idea.
KNOWN ISSUES:
  - Timing: a live pipeline takes ~3.5-4.5 min; RQ job_timeout is 600 s and the reaper cutoff 900 s. Repairs on a slow
    model can exceed 600 s (job -> FAILED MODEL_TIMEOUT, retryable). Recommend a per-queue timeout for `agents`.
  - #5 wizard is not fully atomic (dispatch failure leaves a FAILED campaign); no Idempotency-Key on /generate and
    /wizard yet (same gap as sources).
  - #7 C3 guard plan_complete is enforced by the orchestrator path, not the machine; C4 reason records the error
    code but not the failing agent_run id (the agent run itself is FAILED with the same correlation id).
  - Planner idea_ref beyond the selected mix becomes content_idea_id NULL rather than a repair.
  - plan_items unique (campaign, horizon, ordinal) includes archived rows, so a replan replaces the plan; the prior
    plan survives only in the planner agent_run output. /replan, strategy PATCH/regenerate, 14/30-day plans,
    BUDGET_EXCEEDED pre-flight and ResearchProvider interface are not built yet.
  - Gate fixtures are hand-authored canonical answers (schema-valid by construction); the live run is the real check.
DECISIONS NEEDED: none blocking. Optional: per-queue job timeout for agents (see above).
NEXT PHASE: 6 — Generation + QA + versioning
```

## 2026-09-24 — Phase 6 debug-loop entries
1. Symptom (self-review before running): QA DUPLICATE_HASHTAG would fire on every post. Cause: `_hashtags`
   counted Draft.hashtags and again the tags already composed into the segments. Fix: count only published
   segments. Verify: tests/unit/test_content_qa.py clean-post case PASS on all stages.
2. Symptom: unit test for BROKEN_URL failed. Cause: test used `www.broken`, which has a valid-looking host
   (test bug, not code). Fix: test uses `https://brokenlink/page`. Verify: 15 passed.
3. Reviewer MAJOR #1: DRAFT assets could be stranded (failed writer / dead dispatch; plan item counted as done
   forever). Fix: generate-assets re-claims DRAFT assets of the selected plan items for the new request
   (generation_request_id update, not a status write); old job's CA2 fence then drops stale results.
   Verify: test_failed_drafts_are_claimed_by_the_next_request (5 generated + 3 failed, then 3 generated, 8 total).
4. Reviewer MAJOR #2: accuracy regexes — invented figures matched as substrings ("5%" inside "35%"), bare domains
   unchecked, allowed citations failing on www/http/utm variants. Fix: whole-figure comparison, bare-domain
   pattern (email-safe), URL key = host without www + path (query/fragment/trailing slash ignored).
   Verify: 5 regression unit tests.
5. Live evidence: generation of 7 assets took 498 s (RQ job_timeout 600 s, reaper 900 s) and a 13-item plan took
   941 s. Cause: one timeout for all queues. Fix: per-queue RQ budgets (agents 1800 s, generate 3600 s, qa 900 s,
   default 600 s); reaper cutoff per job kind = budget + 300 s; reaper also closes orphaned agent runs of
   ASSET_GENERATION / QA_RUN. Verify: unit test (reaper cutoff > budget for every JobKind); final live run 941 s OK.

```
PHASE 6 — Generation + QA + versioning — GATE_PASSED (commit pending)
COMPLETED:
  - Writers (LinkedIn, X): prompts content/{linkedin,x}_writer@v1 (PRIVATE, untrusted-source markers), schemas
    LinkedInPost / XPost (no length limits in the schema so over-length reaches QA), deterministic composition
    (VersionFields -> build_draft) shared by generated and human-edited versions; X threads stored with "---"
  - content_versions (§36): immutable versions with prompt_version_id + model_run_id + agent_run_id (AgentOutcome
    now carries them); HUMAN_EDIT via POST /content/{id}/versions (CA16, lost-update 409, approval_actions EDIT,
    QA_RUN job); GET versions / version / diff (field-level + unified)
  - QA stages 1-4 (§11), deterministic, config/qa.yaml (limits UNVERIFIED): STRUCTURAL (hook, body, CTA when the
    plan item requires one — field or inline, thread shape), BRAND (banned FAIL, avoid WARN, duplicate/fatigue
    WARN), ACCURACY (links must be packet citations or the offer URL, claim refs exist, figures in evidence,
    unsourced quotes WARN), PLATFORM (format, per-post/text length, hashtags, malformed URLs, markdown)
  - Duplicate/fatigue (§10): EmbeddingProvider (LM Studio /v1/embeddings; FakeProvider hashing embedder),
    routed embed_texts (PRIVATE, LOCAL_ONLY), FULL + HOOK vectors per version in pgvector, cosine neighbours among
    the brand's live current versions, configurable thresholds, warn-only, GET /content/{id}/similar; QA degrades
    to DUPLICATE_CHECK_SKIPPED (WARN) when no embedding model is reachable
  - POST /campaigns/{id}/generate-assets (ACTIVE only; CA1; ASSET_GENERATION job; per-asset writer failures leave
    DRAFT and are reported; skipped = platforms without an MVP writer), GET /content, /content/{id}, qa-results
  - Job handlers ASSET_GENERATION and QA_RUN; per-queue timeouts; reaper per kind
TESTS (run this session, 2026-09-24):
  uv run poe check -> 165 files already formatted / All checks passed! / Success: no issues found in 120 source
                      files / Contracts: 4 kept, 0 broken. / 526 passed, 66 deselected in 5.82s
  uv run poe test-int -> 63 passed, 529 deselected in 21.64s
  Gate (unit, tests/unit/test_content_qa.py 20 tests): MISSING_CTA, BANNED_PHRASE, CITATION_NOT_IN_PACKET, OVER_LENGTH
    (X post > 280, incl. CTA appended to the last post) all FAIL; near-duplicate fixture pair >= 0.90 -> WARN
  Gate (integration, tests/integration/test_content_generation.py 4 tests): 8 assets from the fixture plan ->
    4 AWAITING_APPROVAL, 4 QA_FAILED with OVER_LENGTH / BANNED_PHRASE / CITATION_NOT_IN_PACKET / MISSING_CTA;
    NEAR_DUPLICATE WARN on the duplicate pair pointing at the original (still AWAITING_APPROVAL); /similar lists it;
    every version has prompt + model provenance and 4 QA rows; human edit -> v2 HUMAN_EDIT -> QA_RUN -> AWAITING_APPROVAL,
    diff = [cta], v1 QA kept, stale edit 409, VIEWER 403; generate-assets on a non-ACTIVE campaign 409
  LIVE (LM Studio qwen/qwen3.5-9b + text-embedding-nomic-embed-text-v1.5, final code):
    tests/integration/test_live_content.py -> 1 passed in 1204.25s (0:20:04); docs/audit/phase6_live_content.json:
    planning 261.6 s, generation 940.9 s for 13 plan items, 13 generated / 0 failed, all AWAITING_APPROVAL
    (WARNs only), embeddings 768-dim nomic
  alembic heads -> 83579cc9b1d2 (head) (no schema change this phase)
REVIEWER: CRITICAL 0 · MAJOR 2 · MINOR 9
  Fixed: MAJOR #1 stranded DRAFT assets, MAJOR #2 accuracy regexes; MINOR #3 LinkedIn hook/hashtag duplication,
  #4 X whole-tag matching, #5 X edit hook from first post, #6 inline CTA accepted, #10 neighbours = live current
  versions only.
KNOWN ISSUES:
  - Near-duplicate threshold 0.90 was calibrated on the fake embedder; with real nomic vectors same-campaign posts
    often score >= 0.90 (live run: 7 of 13 assets got NEAR_DUPLICATE warnings). Warn-only, but thresholds should be
    calibrated per embedding model (config/qa.yaml) before relying on them.
  - #7 a QA error after CA2 leaves the asset GENERATED without an automatic QA_RUN (re-run via edit for now);
    #8 editing an APPROVED asset does not record invalidated_approval_of (Phase 7 approvals); #9 asset row lock is
    held during the embedding call; #11 X counts raw URL length, not the 23-char t.co length (limits UNVERIFIED).
  - Not built yet: POST /content (manual create) + /submit (CA3), /regenerate (CA18/19), /qa re-run endpoint,
    auto-approve (CA7 by SYSTEM), LLM-based tone/brand QA (Stage 2 is rule-based), per-model HNSW index.
  - Writers use REASONING_LOCAL (same model as planning) to avoid GPU model swapping; FRONTIER_CREATIVE later.
DECISIONS NEEDED: none blocking. Optional: near-duplicate thresholds for nomic (e.g. 0.95 FULL / 0.97 HOOK).
NEXT PHASE: 7 — Approval, scheduler, mock publish, mock analytics -> MVP GATE
```

## 2026-09-25 — Phase 7 debug-loop entries
1. Symptom: tests/unit/test_scheduler_process.py hung after the outbox was added to the tick. Cause: the tick now
   called run_once, which reached for the (unroutable) test database; the fixture only stubbed ping/reaper.
   Fix: stub run_once in the autouse fixture (unit tests never touch a DB). Verify: 5 passed in 0.69s.
2. Symptom: test_publication_uncertain... passed alone, failed in the module run (publisher.calls == 2).
   Cause: the scheduler is global; later ticks also published other tests' leftover due jobs in the shared DB,
   and an injected fault could be consumed by a foreign job. Fix (tests + mock): MockPublisher records requests and
   can scope faults to content versions; assertions count only the test's own versions. Verify: 10 passed.
3. Symptom: release tick in the uncertain test claimed nothing. Cause: test bug - the "never auto-retry" ticks
   correctly re-held the second job each time (guard 4), moving its due_at. Fix: release at the job's due_at.
4. Symptom: new late-result tests saw PENDING in the module run. Cause: the simulated mid-call lease recovery
   fired while publishing another test's job, so our not-yet-dispatched job correctly got J10. Fix: scope the
   simulation to the test's version. Verify: 14 passed.
5. Audit ordering: state transitions in one transaction shared now() (txn start), so timeline order was undefined.
   Fix: audit rows use clock_timestamp(). Verify: the E2E asserts the exact DRAFT..PUBLISHED sequence.

```
PHASE 7 — Approval, scheduler, mock publish, mock analytics — MVP GATE PASSED (commit pending; Phase 6 also)
COMPLETED:
  - Approvals (§37): GET /approvals, POST /approvals/{id}/actions (APPROVE needs 4 QA rows with no FAIL on the
    current version; REJECT/REQUEST_REVISION need a comment; SCHEDULE/PUBLISH_NOW queue a job, never publish),
    GET /approvals/{id}/history; every action writes approval_actions with the actor
  - Outbox scheduler (A3, STATE_MACHINES §3.4): claim = SELECT ... FOR UPDATE SKIP LOCKED + J2/J3 through the state
    machine (no raw status SQL); dispatch start J4 committed before I/O with the lease checked by the database
    clock; P1 publication; outcome fenced on claim_token (J5/P2/CA15, J7 backoff+jitter, J6/P3/CA14, J8/P6);
    lease recovery J10/J11 decided by the job's own publish_mode; late success after J11 -> J12; late results
    after the job moved on -> audit event publication.late_result; guard 4 hold (J7b) in MOCK and LIVE; live
    guard §3.3 incl. the independent ENV=test block; a lost race never aborts the tick
  - MockPublisher (§34): synthetic ids idempotent per key, deterministic fault injection (rate limit, pre-send
    timeout, after-send, auth), deterministic platform-shaped metrics (seeded by content hash)
  - Publications API: jobs list/get/cancel (J9+CA13), publications list/get, reconcile (MANUAL_CONFIRM_PUBLISHED by
    EDITOR -> J12b/P5b/CA15; MANUAL_CONFIRM_NOT_PUBLISHED OWNER-only -> J13/P7b/CA14); platform accounts (OWNER
    create, never live); the scheduler process runs the outbox every tick (config/publishing.yaml)
  - Analytics (D1 slice): POST /analytics/pull (ANALYTICS_PULL job, mock), POST /analytics/snapshots (fixture
    ingest, NULL kept), snapshots list/get, GET /analytics/campaigns/{id}/report with §16 attributes and §17 rates
    (per-row NULL-safe; aggregates only over inputs every row captured, missing inputs listed)
  - Optimization Agent: prompt analytics/optimizer@v1 over a labelled report (S1..Sn); a recommendation is stored
    only when every cited label resolves to a stored snapshot (others dropped + counted); POST
    /recommendations/generate, GET /recommendations; GET /audit/timeline/{entity_type}/{id}
TESTS (run this session, 2026-09-25):
  uv run poe check -> 187 files already formatted / All checks passed! / Success: no issues found in 137 source
                      files / Contracts: 4 kept, 0 broken. / 541 passed, 82 deselected in 6.77s
  uv run poe test-int -> 78 passed, 545 deselected in 41.92s
  §55 E2E (tests/integration/test_e2e_success_scenario.py, FakeProvider fixtures): all 16 steps + audit trail;
    YOUTUBE/INSTAGRAM plan items reported skipped NOT_IMPLEMENTED (D2); duplicate SCHEDULE 409; X profile_visits NULL
    not 0; follow_conversion null_reason "profile_visits not captured"; 2 recommendations stored, 1 (cites S9)
    dropped, every evidence id resolves via GET /analytics/snapshots/{id}; timeline DRAFT..PUBLISHED with actors
  Gate: three schedulers racing (asyncio.gather) -> one platform call and one publication per job, disjoint claims;
    forced SKIP LOCKED test (a row locked elsewhere is skipped, not waited on)
  Gate: PUBLICATION_UNCERTAIN path -> job UNCERTAIN, publication PUBLICATION_UNCERTAIN, asset stays SCHEDULED, account
    has_open_uncertain, the other job on the account held (attempt not counted), no auto-retry over 3 more ticks,
    EDITOR "not published" 403, OWNER -> FAILED/APPROVED, account released; confirm-published path; late success J12
  Also: rate-limit retry with backoff (attempt 2 succeeds), auth failure final, lease recovery J10/J11, DB-clock
    lease refusal (nothing sent), A6 dispatcher ENV=test block (LIVE forced in-process -> LIVE_PUBLISH_BLOCKED,
    zero platform calls)
  LIVE §55 (LM Studio qwen/qwen3.5-9b + nomic embeddings; publishing MOCK), final code:
    tests/integration/test_live_e2e.py -> 1 passed in 554.81s (0:09:14); docs/audit/phase7_live_e2e.json: planning
    204 s, 5 posts generated in 295 s (INSTAGRAM skipped), 5 approved/scheduled/published, 5 snapshots, 4
    recommendations stored with valid evidence, 0 dropped. An earlier live run on pre-review code also passed
    (1 passed in 935.74s: 8 generated, 2 correctly QA_FAILED, 6 published, 4 recommendations).
REVIEWER (R full review): CRITICAL 0 · MAJOR 3 · MINOR 10 — no double-publish or UNCERTAIN auto-retry path found
  Fixed: M1 dispatch-start lease check now in SQL against the DB clock; M2 aggregate rates no longer mix rows that
  did and did not capture an input; M3 a lost race on one job no longer aborts the tick; minors #1 J10/J11 by the
  job's publish_mode, #2 late-success/J12 test + deterministic SKIP LOCKED test, #3 late results audited,
  #4 effective_publisher honest (None unless MOCK), #5 unsupported mode -> CONFIGURATION_ERROR, #6 a
  recommendation citing any unknown row is dropped entirely, #7 fresh MockPublisher per tick, #9 tick interval
  from config, #10 reconcile accepts published_at.
KNOWN ISSUES:
  - No automatic reconciliation (J8 "enqueue reconciliation"): every UNCERTAIN needs a human until adapters can
    look posts up (Phase 13). Idempotency-Key header replay ([IK]) not implemented; the A3 unique index still
    prevents duplicate jobs. EDIT/REGENERATE via /approvals return VALIDATION_FAILURE reason NOT_IMPLEMENTED
    (API_PLAN wants 501). Rescheduling a SCHEDULED asset (CA12) = cancel + schedule. Calendar, attribute
    comparisons, derived-metric definitions API and the content-efficiency metric: Phase 8. Manual export
    publisher and mark-published / not-published / deleted: later phase.
  - Claim/lease timestamps use the scheduler's clock (the dispatch-start lease check uses the DB clock).
  - The live planner sometimes picks a format a platform does not support (LinkedIn THREAD); QA rejects it
    (UNSUPPORTED_FORMAT). The planner schema could constrain formats per platform.
  - Recommendation.status is written only by its server default (PROPOSED); no recommendation state machine yet.
  - Phase 6 near-duplicate threshold calibration still open.
DECISIONS NEEDED: none blocking. MVP milestone reached.
NEXT PHASE: 8 — Analytics (per docs/PHASES.md)
```

## 2026-09-25 — Post-MVP known-issue pass (commit pending)
Debug-loop entries:
1. Symptom: E2E and a publishing test expected 409 for a second SCHEDULE of a SCHEDULED asset.
   Cause: CA12 reschedule was implemented (the old refusal was the known issue). Fix: tests assert the
   reschedule (old job CANCELLED, new job, audit SCHEDULED->SCHEDULED) and IK replay instead.
2. Symptom: lease-refusal test depended on a stale tick clock. Cause: leases now come from the DB clock.
   Fix: the test expires the lease in the DB (lease_expires_at only, never status). Verify: 15 passed.

```
POST-MVP KNOWN ISSUES — RESOLVED
  - Idempotency-Key (API_PLAN §1.6): idempotency_keys store (services/idempotency.py, own transactions),
    api/idempotency.py wrapper on every [IK] route (sources upload/url/retry, wizard when generate=true,
    campaigns generate + generate-assets, approvals SCHEDULE/PUBLISH_NOW/REGENERATE, reconcile, analytics
    pull, recommendations generate, content regenerate + qa). Replay -> stored response +
    Idempotent-Replayed: true, no side effects; reused key with other input -> 409 IDEMPOTENCY_KEY_REUSED;
    in progress -> 409; missing -> 400 IDEMPOTENCY_KEY_REQUIRED (setting OMNIGROWTH_REQUIRE_IDEMPOTENCY_KEY,
    default true); failed request forgets its key; scheduler purges expired keys. Test clients send a key.
  - VALIDATION_FAILURE reason NOT_IMPLEMENTED -> HTTP 501; missing/invalid header -> 400 (API_PLAN).
  - §37 complete: EDIT and REGENERATE via /approvals/{id}/actions; REGENERATE (CA18/CA19) also at
    POST /content/{id}/regenerate -> DRAFT + ASSET_GENERATION job -> REGENERATION version (based_on the
    replaced one, instructions from the REGENERATE action); CA17 edit of a SCHEDULED asset and CA12
    reschedule cancel the pending job first (refused while CLAIMED/UNCERTAIN).
  - Approval invalidation recorded: EDIT/REGENERATE put invalidated_approval_of in the audit metadata.
  - Brand auto-approve: CA7 by SYSTEM after CA6, approval_actions row actor SYSTEM:auto-approve.
  - POST /content/{id}/qa re-run; a QA error after CA2 no longer strands the asset: reported as qa_pending
    and, in the worker, re-queued as a QA_RUN job.
  - QA no longer holds the asset row lock during the embedding call (lock taken after, re-checked, fenced).
  - X link length counted as 23 characters (config/qa.yaml url_chars, UNVERIFIED).
  - Near-duplicate thresholds per embedding model (config/qa.yaml similarity.per_model). Calibrated on
    LM Studio nomic: light edit 0.996, same body other CTA 0.982, paraphrase ~0.93, distinct same-topic
    <= 0.91 -> near_duplicate 0.95; hooks identical 1.00, paraphrase 0.80, distinct <= 0.76 -> 0.90.
  - Scheduler claim/lease timestamps from the database clock (func.now()); dispatch start keeps the DB-side
    lease check; lease recovery counts a lease expired by either clock.
  - Planner formats constrained per platform (config/qa.yaml formats; e.g. LinkedIn THREAD -> TEXT_POST),
    counted as formats_adjusted in the planning job result.
  - RECOMMENDATION state machine (RC1-RC3, STATE_MACHINES §7.5); recommendations created through it;
    POST /recommendations/{id}/accept|dismiss (EDITOR, final, decided_by recorded).
TESTS (run this session, 2026-09-25):
  uv run poe check -> 190 files already formatted / All checks passed! / Success: no issues found in 139 source
                      files / Contracts: 4 kept, 0 broken. / 548 passed, 89 deselected in 6.18s
  uv run poe test-int -> 85 passed, 552 deselected in 51.00s (new: test_known_issues.py 6, publishing +2)
  LIVE §55 on this code: tests/integration/test_live_e2e.py -> 1 passed in 553.63s (0:09:13); planning 205.5 s,
    5 generated in 289.1 s (INSTAGRAM, YOUTUBE skipped), 5 approved/scheduled/published, 4 recommendations
    stored, 0 dropped (docs/audit/phase7_live_e2e.json)
STILL OPEN (by design, see docs/FOLLOW_UPS.md): automatic reconciliation and ManualExportPublisher (need
  real platform APIs, Phase 13); LLM tone QA; HNSW index; replan/14-30 day plans/budget pre-flight; rate
  limits and ops hardening before any remote deployment.
NEXT: docs/FOLLOW_UPS.md step 1 — Phase 8 Analytics depth.
```

## 2026-09-26 — Human decisions, safeguard audit, Phase 8 gate (commits pending: signing)
Human decisions (AskUserQuestion): DOCX ratio 100:1 per member -> SPEC_AMENDMENTS A13; LM Studio
only (Ollama removed from role routing); writers stay on REASONING_LOCAL; ADR-0003 already ACCEPTED.
Decision commit attempted with explicit paths: failed at SSH signing (passphrase) -> human commits.

Safeguard audit (docs/audit/safeguards_post_mvp.md). NEW: streamed ZIP byte counting + declared-size
mismatch rejection; DTD/ENTITY rejection in every DOCX part (UTF-8/16/32); 120 s parse budget (ZIP
chunks, PDF pages); max_prompt_chars per provider (prompts incl. repairs, embedding inputs);
cross-process Redis inference slots with bounded wait (SLOT_WAIT_TIMEOUT) and TTL; bounded human
backlog per model queue (429, OMNIGROWTH_MAX_OPEN_MODEL_JOBS=20); repo config proven cloud-free
under every routing profile. Verified existing: body limits, URL fetch caps, SSRF, member/total caps,
text caps, timeouts, bounded repairs, outbox duplicate-free recovery. Gaps recorded (doc §3).

Phase 8 (docs/METRICS.md): restricted formula grammar (ast read + whitelist interpreter, never eval),
derived_metric_definitions as data with immutable revisions (GET/POST/PATCH, OWNER), 7 defaults incl.
configurable content_efficiency; GET /analytics/publications/{id}/metrics; GET
/analytics/attribute-comparisons (13 attributes, n/mean/median/sufficient/excluded_null/evidence ids,
"not causal" note); campaign report with since/until/sources, by_format, cited definition revisions,
provenance, sufficiency; GET /calendar (day/week/month, tz, DST-safe); scheduled ANALYTICS_PULL
(config/publishing.yaml analytics); simulated vs real provenance on every analytics response and on
recommendations (evidence_query.simulated_evidence); POST /analytics/snapshots now [IK].

Debug-loop entries:
1. Symptom: test_scheduler_process::test_run_honours_stop TimeoutError. Cause: new tick step
   enqueue_analytics_pulls opened a real DB session (unroutable in unit env); fixture stubbed every
   other step. Fix: stub it in the fixture. Verify: 5 passed.
2. Symptom: test_size_limits hit the depth limit before the node limit. Cause: test built a left-deep
   chain. Fix: balanced tree in the test. Verify: 42 passed.
3. Symptom: KeyError requested_by_type in phase-8 test. Cause: JobRead does not expose it. Fix: read
   the jobs row. Verify: 6 passed.

Reviewer (read-only subagent): 2 MAJOR, 7 MINOR. Fixed: M1 strict+captured metric dropped from
aggregate; M2 constant-bearing formulas (content_efficiency) now aggregate as MEAN_OF_ROWS (size
independent), rates stay RATIO_OF_SUMS; m3 huge constant -> 422 not 500; m4 defaults seeded once in a
committed step (no rolled-back cited ids, no writes after first seed); m5 concurrent create -> 409;
m6 UTF-16/32 DTD markers; m9 embedding size cap falls through to next candidate. Regression tests added.

```
PHASE 8 GATE — PASSED (run this session, 2026-09-26)
  uv run poe check -> 198 files already formatted / All checks passed! / Success: no issues found in
                      144 source files / Contracts: 4 kept, 0 broken. / 607 passed, 99 deselected in 7.81s
  uv run poe test-int -> 95 passed, 611 deselected in 63.58s (0:01:03)
  uv run alembic heads -> 83579cc9b1d2 (head)
  LIVE §55 (LM Studio, Redis slots active): tests/integration/test_live_e2e.py -> 1 passed in 1028.42s
    (0:17:08); planning 244.2 s, generation 710.8 s (10 generated, INSTAGRAM/YOUTUBE skipped),
    10 claimed/succeeded/published, analytics pull SUCCEEDED, 4 recommendations stored, 0 dropped.
KNOWN ISSUES (MINOR, open): slot TTL not renewed / httpx timeout is per-phase (m7); backlog cap
  best-effort without lock (m8); calendar reschedule/plan-item move endpoints not implemented; no
  metric normalisation by post age; PDF time budget checked between pages only.
NEXT: Phase 9 — experiments + optimization (needs human: default min_sample_size / confidence).
```

## 2026-09-26 — Phase 9 (experiments) + pre-gate safeguard fixes — gate BLOCKED (live re-run pending)
Commits 85729f2 (decisions) and a3011dd (Phase 8) were made by the human with SSH signatures
(verified: each object carries an SSH SIGNATURE block; local verification only lacks
gpg.ssh.allowedSignersFile). Signing configuration untouched.

Pre-gate fixes (human request):
- GPU lease (providers/slots.py): renewable lease, self-fencing before expiry (deadlines from
  the SENT time of ACQUIRE/RENEW), settle one lease after any unclean predecessor, clean marker,
  compare-and-delete, orphan-free acquisition (try/finally). Total call deadline in the router.
  9 unit + 4 real-Redis tests incl. "lost ownership never overlaps".
- Backlog cap: pg_advisory_xact_lock per queue; 8 racing requests / 3 places -> exactly 3;
  negative control without the lock -> 8 created (test fails as it should).
- Parsing: worker parses in a spawned child killed at max_parse_seconds + 15 s (was cooperative
  only; Windows RQ TimerDeathPenalty cannot interrupt C code); child watchdog exits with its
  parent (negative control: orphan survives without it); POSIX RLIMIT_AS 2 GiB. Benchmarks
  (scripts/bench_parse.py): 500-page PDF 0.66 s, 6.3 M-char DOCX 5.75 s — 120 s kept as margin
  for real-world PDFs (not measured: no corpus), documented with the p99x3 recommendation.
- Context budget: ceil(chars/3) + output allowance <= context_tokens (32,768 chat; nomic 2,048
  per text) on the request as built (+ response_format schema); unset max_tokens counts 4,096;
  QA embeddings clip explicitly. LM Studio loaded_context_length verified 70,144.

Phase 9 (docs/EXPERIMENTS.md, SPEC_AMENDMENTS A14): unit = one post read from one snapshot at a
fixed age (tolerance <= age/4); floor 30 per variant; power 80 % from REAL baseline sigma and a
pre-registered minimum effect; confidence fixed 0.95; blocked Welch by account; Bonferroni
decisions == simultaneous intervals; pairwise rule among several significant treatments;
fixed endpoint (WINDOW_OPEN before end_at, single terminal evaluation); observational never wins
(DB CHECK); REAL evidence = API/MANUAL on a post that actually went out (MOCK publications are
simulated; ingest refuses MANUAL on MOCK); simulated analysed separately, never selects a winner;
no automatic strategy change. Migration a14e0f9b2c31 (experiment_units, design, assignment),
round-tripped, alembic check clean.

Debug-loop entries:
1. test_scheduler_process timed out -> new tick step not stubbed (earlier pattern) -> stubbed.
2. Control/treatment swapped -> variants ordered by created_at/id (same txn, same-ms UUIDv7) ->
   variant_order frozen in design. Real bug.
3. Start response showed stale design -> Core UPDATE vs identity map -> populate_existing.
4. Reviewer M1-M4 + m5-m16 fixed (see above); m12 (server must stop on disconnect) documented.
5. sync_experiments MissingGreenlet -> read end_at after rollback expired it -> read before.
6. Live §55 FAILED at planning (research MODEL_TIMEOUT after 210 s). Classified ENVIRONMENT:
   LM Studio had qwen3.5-4b-uncensored (not OmniGrowth config) resident next to qwen3.5-9b
   (70,144 ctx); measured 23.1 tok/s vs 91 tok/s in the Phase 5 live run; research output
   3-6 k tokens -> > 180 s. Not patched (debug-loop rule). Evidence:
   docs/audit/phase9_live_e2e_env_failure.json; phase7_live_e2e.json restored to the last pass.

```
PHASE 9 GATE — BLOCKED (live re-run pending; everything else run this session, 2026-09-26)
  uv run poe check -> 210 files already formatted / All checks passed! / Success: no issues found in 149 source files / Contracts: 4 kept, 0 broken. / ==================== 647 passed, 114 deselected in 20.53s =====================
  uv run poe test-int -> =============== 110 passed, 651 deselected in 92.22s (0:01:32) ================
  uv run alembic heads -> a14e0f9b2c31 (head);  uv run alembic check -> No new upgrade operations detected.
  Gate tests: test_no_winner_below_the_sample_threshold_even_with_a_huge_effect,
              test_recommendation_without_evidence_ids_is_rejected -> passed
  LIVE §55: tests/integration/test_live_e2e.py -> 1 failed in 212.70s (MODEL_TIMEOUT, environment)
  Previous live pass (Phase 8, 2026-09-26): 10 posts published by the MockPublisher
    (PUBLISH_MODE=MOCK, external ids mock_*; no platform network call, nothing reached a real
    audience); INSTAGRAM and YOUTUBE skipped (writers NOT_IMPLEMENTED, decision D2).
KNOWN ISSUES: GPU exclusivity after a client-side cancel assumes the server stops generating
  (UNVERIFIED per LM Studio version; 30 s settle mitigates); parser memory cap POSIX-only;
  3 chars/token heuristic; allocation within a block partly predictable (random sizes V/2V).
NEXT: human frees VRAM, re-run the live test; then Phase 10.
```
