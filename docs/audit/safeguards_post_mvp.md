# Upload and inference safeguards — verification record (2026-09-26)

Scope: the human asked that the 100:1 DOCX ratio not be treated as complete ZIP-bomb protection,
and that inference queue limits, timeouts, bounded retries, duplicate-free recovery and
no-silent-cloud-fallback be verified. Each row states what enforces it and which test proves
it. "NEW" = added in this pass. Gates run in this session: `uv run poe check` 559 passed;
`uv run poe test-int` 89 passed.

## 1. Uploads and ingestion

| Safeguard | Enforced by | Evidence (tests) |
|---|---|---|
| Request body cap, streamed, before multipart spooling (JSON 1 MiB, upload 25 MiB + framing); declared Content-Length rejected up front | `api/body_limit.py` (ASGI, below FastAPI) | `unit/test_limits.py::test_declared_oversize_body_is_rejected_before_the_app`, `::test_streamed_oversize_body_without_length_is_rejected` |
| Upload file cap 25 MiB | `api/routes/sources.py` (`read(max_bytes+1)`), `ingestion/_common.check_size` | ingestion parser tests |
| URL fetch: SSRF pre-check and per-redirect check, 10 MiB streamed cap, content-type allowlist, 10 s connect / 30 s total | `security/http.py` | `integration/test_sources_api.py::test_url_precheck_blocks_ssrf_synchronously` and the unit SSRF suite |
| ZIP: member count ≤ 2000 | `check_zip_limits` (central directory) | `test_docx_too_many_members` |
| ZIP: declared size ≤ 50 MiB per member and ≤ 100 MiB in total | `check_zip_limits` | `test_docx_uncompressed_size_limits` |
| ZIP: ratio ≤ 100:1 for members ≥ 1 MiB (A13) | `check_zip_limits` | `test_docx_zip_bomb_compression_ratio` |
| **NEW** — ZIP: actual decompressed bytes counted in 64 KiB chunks against the same caps; a member whose output differs from its header is rejected (CRC checked) | `docx_parser.verify_zip_stream` | `test_docx_member_lying_about_its_size_is_rejected`, `test_docx_streamed_bytes_are_capped_even_below_the_ratio_threshold` |
| **NEW** — XML: `<!DOCTYPE`/`<!ENTITY` encoded as UTF-8, UTF-16 (LE/BE) or UTF-32 (LE/BE) in any DOCX part is rejected before lxml runs (billion laughs, XXE). The marker search spans chunk boundaries. Other encodings (for example EBCDIC or UTF-7) are not scanned. The underlying protection is python-docx's `resolve_entities=False` plus lxml's default no-network setting. | `verify_zip_stream`; python-docx `oxml_parser` | `test_docx_with_dtd_or_entities_is_rejected_before_xml_parsing[billion-laughs, external-entity]`, `test_dtd_marker_split_across_chunks_is_found`, `test_docx_dtd_in_utf16_or_utf32_part_is_rejected[3 encodings]` |
| **NEW** — Processing time: a 120 s wall-clock budget per parse, checked between ZIP chunks and between PDF pages. The outer hard limit is the RQ `ingest` job timeout of 600 s. | `IngestionLimits.max_parse_seconds` | `test_parse_time_budget_is_enforced` |
| PDF: ≤ 500 pages; pypdf's per-stream decompression caps (75 MB, library defaults); encrypted PDFs refused | `pdf_parser.py` | PDF parser tests |
| Extracted text ≤ 2,000,000 characters (checked incrementally for PDFs) | `normalized.build_document`, `pdf_parser` | `max_text_chars` tests |
| HTML: stdlib parser; nothing executed or fetched | `html_parser.py` | HTML parser tests |
| Text sent to the LLM: the source excerpt in planning prompts is capped at 12,000 characters | `campaigns/context.SOURCE_CHAR_LIMIT` | planning tests |
| **NEW** — Token/prompt cap: each request is limited to `max_prompt_chars` (default 64,000 characters, about 16k tokens), counted over all messages including repair turns, and over all inputs of an embedding call. It is refused before any model call. | `ModelRouter.check_input_size` (per provider in `models.yaml`) | `test_oversized_prompt_is_refused_before_any_model_call` |

## 2. Inference

| Safeguard | Enforced by | Evidence |
|---|---|---|
| One GPU call at a time **within a process** | router asyncio semaphore per concurrency group | `unit/test_structured_generation.py` (concurrency group test) |
| **NEW** — One GPU call at a time **across processes** (a second worker, the API probe): Redis slot per concurrency group; the TTL is the provider timeout + 30 s, so a crashed holder frees it; compare-and-delete release | `providers/slots.RedisSlotLimiter`, used by every `build_router` | `integration/test_inference_guards.py` (3 slot tests), `unit/...::test_every_model_call_holds_a_cross_process_slot` |
| **NEW** — Bounded wait for the GPU: `slot_wait_s` (default 300 s), then MODEL_UNAVAILABLE `SLOT_WAIT_TIMEOUT` | `RedisSlotLimiter.slot` | `test_slot_is_exclusive_and_waiters_are_bounded` |
| **NEW** — Bounded backlog: a human request is refused with 429 once `agents`/`generate`/`qa`/`embed` holds `OMNIGROWTH_MAX_OPEN_MODEL_JOBS` (default 20) open jobs. System follow-ups are never refused. | `services/jobs._check_backlog` | `test_model_queue_backlog_is_bounded_for_humans` |
| Timeouts: per model call `timeout_s` (180 s local); per job `agents` 1800 s, `generate` 3600 s, `qa` 900 s, others 600 s; the reaper fails jobs that run past their budget | `models.yaml`, `services/jobs.JOB_TIMEOUT_S`, `workflows/tasks` reaper | provider timeout test, reaper tests |
| Bounded retries: ≤ 2 repair attempts per model; one pass over a finite candidate list; no automatic job retries (a failed job stays FAILED until a human retries it) | `providers/structured.py`, `workflows/tasks.py` | structured-generation repair tests |
| Recovery without duplicate posts: at most one live-or-succeeded job per (version, account) (partial unique index, ADR-0003); claims fenced by `claim_token`; an expired lease becomes UNCERTAIN (never blindly re-dispatched) outside mock mode; UNCERTAIN blocks the account until a human reconciles; late results are reconciled or audited, never re-applied; publisher idempotency key | `publishing/scheduler.py`, the DB index | `unit/test_models_metadata.py::test_publication_jobs_partial_unique_idempotency_index`, `integration/test_publishing.py::test_expired_leases_recover_to_pending_or_uncertain`, `::test_publication_uncertain_blocks_the_account_until_a_human_reconciles`, `::test_late_success_after_lease_recovery_is_reconciled`, `::test_late_failure_after_lease_recovery_is_audited_not_applied`, `::test_claim_skips_rows_locked_by_another_scheduler`, `::test_dispatch_refuses_when_the_database_says_the_lease_is_gone`, `unit/...::test_mock_publisher_is_idempotent_per_key` |
| No silent cloud fallback: LOCAL_ONLY is the default everywhere (settings, brand, campaign, agents); PRIVATE never routes remote; the cloud provider is built only with `OMNIGROWTH_CLOUD_API_KEY` **and** a configured `base_url` (none in the repo); `*-cloud` model names are forced REMOTE; every fallback is logged at WARNING and every call records its provider/model in `model_runs` | `providers/router.py`, `providers/factory.py`, `models.yaml` | `unit/test_providers.py::test_local_only_never_returns_remote`, `::test_private_tasks_never_route_remote`, `::test_cloud_named_model_behind_local_preset_is_remote`, **NEW** `::test_repo_config_never_reaches_a_cloud_model[all profiles]` |

## 3. Remaining gaps (explicit)

1. **python-docx XML parse is not time-sliced.** The DTD ban and the byte caps bound its input to ≤ 100 MiB of entity-free XML, and the ingest job timeout (600 s) is the hard stop. There is no finer interrupt inside lxml.
2. **PDF decompression uses pypdf's 75 MB per-stream defaults.** There is no total across streams beyond pages ≤ 500, the time budget and the job timeout. Tightening it is possible with `pypdf.Configuration` if real uploads show abuse.
3. **The prompt cap is in characters, not tokens.** It uses about 4 characters per token. No tokenizer is loaded for local models. Set `max_prompt_chars` per provider to match the served context length.
4. **BALANCED / QUALITY_FIRST campaigns may use a remote model** once a cloud provider is configured. That is an explicit per-campaign opt-in, and it is logged and recorded in `model_runs`, but it creates no audit event and does not notify a human. Add an audit event when the first cloud provider is enabled (Phase 13 or whenever a cloud key is introduced).
5. **The backlog limit counts rows, not GPU minutes.** Twenty large generation jobs can still take hours. This is acceptable for a single-operator local deployment.
6. **The slot TTL is not renewed.** The TTL is `timeout_s + 30`. httpx's `timeout_s` applies to each phase (connect, read, write), not to the whole call. A call that keeps trickling bytes can therefore outlive its slot, and a second process could then reach the GPU. Cancelling a task while its `SET NX` is in flight can also leave a slot held until the TTL runs out (about 210 s). Fix when it matters: wrap the call in a total `asyncio.timeout` and add a renewal task (reviewer MINOR, Phase 8 gate).
7. **The backlog count is best-effort.** `_check_backlog` counts and then inserts, without a lock. Concurrent human requests can overshoot the cap by up to the number of requests made at once. The count is global across brands, which suits a single-operator deployment.
8. **Redis is a dependency of every model call in workers.** If Redis is unreachable, model calls fail with QUEUE_FAILURE, which is the same failure domain as the job queue itself.
