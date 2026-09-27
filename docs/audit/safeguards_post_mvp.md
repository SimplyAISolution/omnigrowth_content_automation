# Upload and inference safeguards — verification record

**Last updated:** 2026-09-26, during the Phase 9 pre-gate.

**Scope.** The human asked for two things:
- The 100:1 DOCX ratio must not be treated as complete ZIP-bomb protection.
- The following must be verified: inference queue limits, timeouts, bounded retries, recovery without duplicate posts, and no silent cloud fallback.

**Phase 9 pre-gate additions.** The Phase 8 reviewer found two issues, and the human asked for three documents:
- **Fixed:** GPU-lock expiry and the concurrent overshoot of the queue cap.
- **Documented:** the parse budget, how it is enforced, and the context budget.

Each row below says what enforces the safeguard and which test proves it. "NEW" marks something added after the MVP.

## 1. Uploads and ingestion

| Safeguard | Enforced by | Evidence (tests) |
|---|---|---|
| Request body cap, counted while streaming and before multipart spooling (JSON 1 MiB; upload 25 MiB plus framing). A declared Content-Length over the cap is rejected up front. | `api/body_limit.py` (ASGI, below FastAPI) | `unit/test_limits.py::test_declared_oversize_body_is_rejected_before_the_app`, `::test_streamed_oversize_body_without_length_is_rejected` |
| Upload file cap of 25 MiB | `api/routes/sources.py`, `ingestion/_common.check_size` | ingestion parser tests |
| URL fetch: SSRF check before the request and on every redirect, 10 MiB streamed cap, content-type allowlist, 10 s connect / 30 s total | `security/http.py` | `integration/test_sources_api.py::test_url_precheck_blocks_ssrf_synchronously`, unit SSRF suite |
| ZIP: at most 2000 members | `check_zip_limits` (central directory) | `test_docx_too_many_members` |
| ZIP: declared size at most 50 MiB per member and 100 MiB in total | `check_zip_limits` | `test_docx_uncompressed_size_limits` |
| ZIP: compression ratio at most 100:1 for members of 1 MiB or more (A13) | `check_zip_limits` | `test_docx_zip_bomb_compression_ratio` |
| **NEW** — ZIP: the bytes actually decompressed are counted against the same caps. A member whose output differs from its header is rejected (CRC checked). | `docx_parser.verify_zip_stream` | `test_docx_member_lying_about_its_size_is_rejected`, `test_docx_streamed_bytes_are_capped_even_below_the_ratio_threshold` |
| **NEW** — XML: a `<!DOCTYPE` or `<!ENTITY` in any DOCX part is rejected before lxml runs. The scan covers UTF-8, UTF-16 LE/BE and UTF-32 LE/BE, across chunk boundaries. Other encodings are not scanned; underneath, python-docx uses `resolve_entities=False`. | `verify_zip_stream` | `test_docx_with_dtd_or_entities_is_rejected_before_xml_parsing[2]`, `test_dtd_marker_split_across_chunks_is_found`, `test_docx_dtd_in_utf16_or_utf32_part_is_rejected[3]` |
| **NEW** — Parse time: a cooperative 120 s budget (checked between ZIP chunks and PDF pages) **plus a hard kill**. The worker parses in a child process that is killed at 120 + 15 s (see §4). | `IngestionLimits.max_parse_seconds`, `ingestion/isolation.py` | `test_parse_time_budget_is_enforced`, `test_parse_isolation.py::test_a_parser_stuck_in_one_step_is_killed_at_the_budget` |
| **NEW** — Parser memory and lifetime: the parse child's address space is capped at 2 GiB (POSIX `RLIMIT_AS`). A crashed child is reported as `PARSER_CRASHED`. A watchdog thread in the child exits it when its parent dies, so no orphan keeps parsing. Error `retryable` flags survive the process boundary. | `ingestion/isolation.py` | `test_a_child_that_dies_is_reported_not_waited_for`, `test_the_parse_child_exits_when_its_parent_dies` (negative control: fails without the watchdog), `test_retryable_flag_survives_the_process_boundary`. The memory cap itself is POSIX-only: see §5. |
| PDF: at most 500 pages; pypdf's per-stream decompression caps (75 MB); encrypted PDFs refused | `pdf_parser.py` | PDF parser tests |
| Extracted text at most 2,000,000 characters (checked page by page for PDFs) | `normalized.build_document`, `pdf_parser` | `max_text_chars` tests |
| HTML: stdlib parser only; nothing is executed or fetched | `html_parser.py` | HTML parser tests |
| **NEW** — Context budget: a request is refused before any model call unless `ceil(input_chars / 3) + max_tokens <= context_tokens` (see §4). The count covers the request **as built**: every message including the appended schema instruction, plus the `response_format` schema. An unset `max_tokens` counts as `default_output_tokens` (4,096). The capability probe is checked too. Embeddings are checked per text, and QA clips long texts explicitly. | `ModelRouter.check_input_size`, `structured.request_texts`, `embed_texts(clip=True)` | `test_oversized_prompt_is_refused_before_any_model_call`, `test_context_budget_counts_the_output_allowance`, `test_embedding_budget_is_per_text_and_clipping_is_explicit` |

## 2. Inference

| Safeguard | Enforced by | Evidence |
|---|---|---|
| One GPU call at a time **within a process** | Router asyncio semaphore per concurrency group | `unit/test_structured_generation.py` (concurrency group test) |
| **NEW** — One GPU call at a time **across processes**, as a renewable Redis lease (§3) | `providers/slots.RedisSlotLimiter`, used by every `build_router` | `unit/test_inference_slots.py` (9 tests), `integration/test_inference_guards.py` (4 slot tests) |
| **NEW** — Bounded wait for the GPU: `slot_wait_s` (300 s), then MODEL_UNAVAILABLE `SLOT_WAIT_TIMEOUT` | `RedisSlotLimiter` | `test_slot_is_exclusive_and_waiters_are_bounded`, `test_lease_is_renewed_while_a_call_outlives_it` |
| **NEW** — A total deadline per call of `timeout_s` + 5 s. httpx timeouts apply per phase, so this deadline is what stops a trickling response. | `router._deadline` | `test_a_call_past_its_total_deadline_is_cut` |
| **NEW** — Bounded backlog: human requests get 429 once a model queue holds `OMNIGROWTH_MAX_OPEN_MODEL_JOBS` (20) open jobs. The count and insert are serialised per queue by `pg_advisory_xact_lock`, so concurrent requests cannot overshoot. System follow-ups are never refused. | `services/jobs._check_backlog` | `test_model_queue_backlog_is_bounded_for_humans`, `test_concurrent_requests_cannot_overshoot_the_backlog_cap` (8 racing requests, 3 places → exactly 3). Negative control: with the lock removed, the test fails (8 created). |
| Timeouts: per job `agents` 1800 s, `generate` 3600 s, `qa` 900 s, others 600 s. The reaper fails jobs that run past their budget. | `services/jobs.JOB_TIMEOUT_S`, reaper | reaper tests |
| Bounded retries: at most 2 repairs per model; one pass over a finite candidate list; no automatic job retries | `providers/structured.py`, `workflows/tasks.py` | repair tests |
| Recovery without duplicate posts: partial unique index, `claim_token` fencing, UNCERTAIN instead of blind re-dispatch, a human reconciles, publisher idempotency key | `publishing/scheduler.py`, DB index | `test_publishing.py` lease, uncertain and late-result tests; `test_publication_jobs_partial_unique_idempotency_index` |
| No silent cloud fallback: LOCAL_ONLY is the default everywhere; PRIVATE never goes remote; the cloud provider is built only with a key **and** a `base_url` (the repo has none); every call records its provider and model | `providers/router.py`, `factory.py`, `models.yaml` | `test_local_only_never_returns_remote`, `test_private_tasks_never_route_remote`, `test_repo_config_never_reaches_a_cloud_model[all profiles]` |

## 3. GPU lease design (fixes reviewer m7)

**Requirement:** a second GPU call must never start while the previous one is still executing, including when the previous holder has lost ownership.

1. **Acquire** is an atomic Lua script. It sets the key with our token and a TTL of `slot_lease_s` (30 s). It also consumes a `:clean` marker if one is present.
2. **Renew** every lease/3 (10 s) with compare-and-pexpire, which extends the TTL only while the key still holds our token.
3. **Self-fencing.** The holder cancels its own call (MODEL_UNAVAILABLE `SLOT_LOST`) in two cases:
   - a renewal finds the key gone or owned by another token, which is detected within lease/3;
   - it cannot confirm ownership for lease − lease/3 (Redis unreachable). This measures from the moment the last successful renewal was *sent*, so the call is cancelled **before** the key can expire and before anyone else can acquire it.
4. **Settle.** A clean release (the call returned, or failed with an error that proves the server answered) leaves a `:clean` marker. An acquirer that does not find the marker waits one full lease (30 s) before starting. That covers a predecessor that crashed, timed out, was cancelled, lost its lease, or lost its Redis data. Any such predecessor has by then either seen the loss at a renewal or cancelled itself.
5. **Release** is a compare-and-delete, so it never frees someone else's lease. A failure or cancellation during acquisition releases whatever it might have taken (try/finally, no broad except).
6. **Timing.** The first self-fencing deadline counts from when the winning ACQUIRE was **sent**. Every later one counts from when the confirming RENEW was sent.

**Proof:**
- `test_lost_ownership_cancels_the_call_before_anyone_else_starts`: the key and marker are wiped mid-call, a new acquirer arrives, and the calls never overlap because the old call ends before the new one starts. The same is tested on real Redis in `test_real_redis_lease_renewal_and_loss_never_overlap`.
- `test_redis_outage_self_cancels_before_the_lease_could_expire`
- `test_takeover_by_another_token_cancels_the_holder`
- `test_cancellation_during_acquisition_leaves_no_orphan`
- `test_many_workers_never_overlap`
- `test_lease_is_renewed_while_a_call_outlives_it`: a call 3× the lease keeps its slot.

**Assumptions:**
- **(a)** No holder process is frozen (suspended VM, laptop sleep, stop-the-world pause) for longer than lease/3 = 10 s. A frozen process cannot cancel anything; that limit applies to any lease protocol without server-side fencing.
- **(b) Server-side stop: now measured, and enforced by an idle check.**
  - **Cancellation** (`scripts/probe_cancellation.py`, `docs/audit/live/cancellation_20260927T022219Z.*`): a long non-streaming generation (our production mode) was cancelled at 4.34 s.
    - LM Studio logged "Client disconnected. Stopping generation…", then `cancel task` and `slot release … n_tokens = 386`.
    - `lms ps` reported the model `idle` by 4.69 s.
    - GPU utilization fell from 93% to 2% within ~1 s.
    - The same log line warns that a prompt already being processed is finished first.
  - **Lease loss on the real server** (`tests/live/test_gpu_exclusivity_live.py`, `docs/audit/live/gpu_exclusivity_*.json`): caller A generated through the production router with a real Redis lease; its lease was deleted mid-generation.
    - A was cancelled with `SLOT_LOST` at 10.05 s, and the server logged the disconnect and stop.
    - `lms ps` reported idle from 10.55 s, with **zero busy samples** between A's cancel and caller B's start.
    - B started at 33.98 s, after the 30 s settle **and** a verified-idle check.
  - **Enforcement.** Holding the lease is not enough to start a call. The router also requires `lms ps --json` to report every loaded model `idle` with `queued == 0` on two consecutive samples (`providers/idle.py`, config `idle_check: lmstudio_cli`, about 0.33 s per probe).
    - If the server stays busy for longer than `slot_wait_s`, the call fails with MODEL_UNAVAILABLE `SERVER_BUSY`.
    - If idleness cannot be verified (CLI missing, server unreachable), the call is **blocked** (`IDLENESS_UNVERIFIABLE`). It never starts on an assumption.
  - **What is and is not claimed:**
    - Among OmniGrowth callers, a call starts only after the previous one stopped **as reported by the server**.
    - LM Studio serves `parallel` (4) requests and other applications may use it. A client outside OmniGrowth that starts between our idle check and our request can still overlap. **Non-overlap with non-OmniGrowth clients is not guaranteed.** Setting LM Studio's parallel slots to 1, or giving OmniGrowth a dedicated server, would remove that window.
  - The idle check uses the host's `lms` CLI. A Docker worker without it is blocked (fail closed) until a host-side check is available. That is a known limitation (§5).

## 4. Why these budgets

### Parse budget: 120 s rather than 10 s — and does it really stop parsing?

**Measured** on this machine on 2026-09-26, with synthetic inputs at the ingestion limits (`uv run python scripts/bench_parse.py`):

| Input | Size | Parse time |
|---|---|---|
| PDF, 100 pages of text | 0.3 MB | 0.14 s |
| PDF, 500 pages (page limit), 1.19 M characters | 1.3 MB | 0.66 s |
| DOCX, 2,000 paragraphs, 0.63 M characters | 0.1 MB | 0.57 s (stream check 0.00 s) |
| DOCX, 20,000 paragraphs, 6.3 M characters (3× the text cap) | 1.1 MB | 5.75 s (stream check 0.02 s) |

**What this means:**
- By these numbers, a **10 s budget would pass every synthetic worst case**.
- 120 s was kept because synthetic PDFs have a single simple font and no layout, and real PDFs are not like that. pypdf text extraction on real documents (CID fonts, many small text objects, form XObjects) is commonly 10–100× slower per page. At the 500-page limit that reaches tens of seconds.
- **Not measured:** no real-world PDF corpus exists on this machine, so the 120 s figure is a margin, not a measurement. The budget is one constant (`IngestionLimits.max_parse_seconds`). Lowering it to 10–30 s is a one-line change once real uploads have been timed. Recommendation: record parse durations in the ingest job result, then set the budget to the observed p99 × 3.

**Is parsing actually terminated?**
- **Before this pass: no.** The budget was cooperative, checked only between ZIP chunks and PDF pages, so a single long C-level step (lxml parsing `document.xml`, one pypdf filter) was not interrupted. The RQ job timeout was not a reliable stop either:
  - On **Windows**, `TimerDeathPenalty` injects an async exception that only takes effect between Python bytecodes.
  - On **Linux/Docker**, the parent RQ worker kills the work-horse process, but only at job timeout (600 s) plus RQ's grace period.
- **Now: yes, on every OS.** Worker-side parsing (uploads and URLs) runs in a `spawn` child process that the parent kills at `max_parse_seconds + 15 s` (`ingestion/isolation.py`).
  - Start-up cost is measured at about 0.4 s on Windows.
  - Tested: a child stuck in a 600 s step is killed at the 5 s test budget, and its PID is verified gone.
  - Pasted text (≤ 1 MiB, plain-text parser) still parses in the API process.

### Context budget: exact server tokenization (replaces characters ÷ 3)

- **Enforcement** (`providers/tokens.py`, `router.check_chat_budget`): LM Studio's own SDK renders the request with the loaded model's **chat template** (system/user markers, the `<think>` prefix) and **tokenizes** it with the model's vocabulary. It also reads the context length the model was **actually loaded with**.
- A request is refused before it is sent unless:

  prompt tokens (template included) + response-schema tokens + output allowance ≤ min(configured, loaded) context

  - The output allowance is `max_tokens`, or 4,096 when unset.
  - Schema tokens are counted conservatively: whether a server puts the schema into the prompt varies.
- **Verified against the server** (`tests/live/test_token_budget_live.py`, `docs/audit/live/token_budget_*.json`). For each input, our count equals LM Studio's own `usage.prompt_tokens` for the same request:

  | Input | Characters | Exact tokens (= server) | Old chars ÷ 3 |
  |---|---|---|---|
  | English | 53 | 20 | 18 (under) |
  | Chinese | 116 | 66 | 39 (under) |
  | Arabic | 240 | 79 | 80 |
  | Emoji incl. ZWJ sequences and flags | 222 | 358 | 74 (under ~5×) |
  | Long URLs | 652 | 359 | 218 (under) |
  | System + user, mixed scripts, URL | 408 | 215 | 136 (under) |

  The old estimate under-counted in 5 of 6 cases, which is why it no longer enforces anything.
- **No loading on the counting path:** the counter uses only models that are **already loaded** (`list_loaded`). The SDK's `model()` call would load a missing model onto the GPU outside OmniGrowth's lease and idle check, so it is never used (review B1). If a configured model is not loaded, the call is refused with `MODEL_NOT_LOADED` and the fix is stated: `lms load <model> --context-length <context_tokens>`.
  - **Operator requirement:** load the chat model with 32,768 context. The embedding model loads just-in-time with a 1-hour TTL in LM Studio. Once it unloads, embeddings are refused until it is loaded again; QA's duplicate check is then skipped and logged (warn-only). Verified 2026-09-27: counting an unloaded model is refused and nothing is loaded.
- **Approximate counting** remains only for providers configured with `budget_counting: approximate`: unit tests, fixture replay and future remote APIs. Every such result is labelled `counted_by: approximate`, `exact: false`. A provider configured for exact counting **never** falls back to an estimate; if the counter is missing, the call is a configuration error, and if the counter is unreachable, the call is refused (`TOKEN_COUNT_UNAVAILABLE`).
- **Embeddings:** checked per text with the embedding model's tokenizer (nomic loaded context 2,048). QA clips long texts by exact count: it takes an approximate prefix, then shrinks it until the exact count fits. It logs which inputs were clipped.
- **Loaded context:** verified 32,768 for `qwen/qwen3.5-9b` after reloading it with `lms load … --context-length 32768` on 2026-09-27. The earlier run had it at 70,144 next to a second model. Because the server-reported loaded value is used whenever it is smaller than the configured one, a smaller load is now enforced automatically.

## 5. Remaining gaps (explicit)

1. **The parser memory cap is POSIX-only.** Windows has no `RLIMIT_AS`. There, a parse child is bounded by the byte caps, the DTD ban and the hard-kill timeout, but not by a memory ceiling. A Job Object limit would add one; not done.
2. **PDF decompression uses pypdf's 75 MB per-stream defaults.** Across streams it is bounded only by the page count, the time budget (now a hard kill) and the memory cap on POSIX.
3. **Exact counting depends on LM Studio's SDK** (a websocket on the same server). Other servers would need their own counter; until then they use the labelled approximate estimate, which under-counts for emoji, CJK and URLs (§4).
4. **GPU exclusivity has limits** (§3):
   - A holder frozen for longer than lease/3 cannot self-cancel.
   - Non-OmniGrowth clients of LM Studio are outside the lease; only the idle check sees them, and that check has a small race window.
   - A Docker worker without the `lms` CLI is blocked until a host-side idle check exists.
5. **BALANCED / QUALITY_FIRST campaigns may use a remote model once a cloud provider is configured.** This is an explicit opt-in and is logged, but no audit event is written. It is owned by Phase 13.
6. **The backlog limit counts jobs, not GPU minutes.**
7. **Redis is a dependency of every model call in workers.** Redis down means QUEUE_FAILURE before the call, or SLOT_LOST during it.
