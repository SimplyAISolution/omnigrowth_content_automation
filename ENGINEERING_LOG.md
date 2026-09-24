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
