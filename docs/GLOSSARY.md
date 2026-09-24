# GLOSSARY.md — canonical names (owned by primary agent)

Every Phase 1 document MUST use these exact identifiers. If a document needs a name that is not here, it proposes it in its "Open questions" section; the primary agent adds it here during reconciliation. Enum values are `UPPER_SNAKE`; table names are `lower_snake` plural; Pydantic schemas are `PascalCase`.

## 1. Conventions
- Primary keys: `id UUID` (UUIDv7 generated app-side; time-ordered for index locality).
- Timestamps: `timestamptz`, UTC, columns `created_at`, `updated_at`.
- Tenancy: **single-tenant, multi-brand**. Almost every domain row carries `brand_id` (FK `brands.id`).
- Status columns are named `status` and typed by a Postgres enum named after the entity (`campaign_status`, `content_asset_status`, …). Only the state-machine module writes them (AGENTS.md §4).
- Soft delete: `archived_at timestamptz NULL` where deletion must be recoverable; no hard deletes of content, publications, or audit rows.
- API: all routes under `/api/v1`. Schemas: `<Entity>Create`, `<Entity>Update`, `<Entity>Read`, list responses `Page[<Entity>Read]` (cursor pagination: `items`, `next_cursor`). Errors: `ErrorResponse { code: ErrorCode, message, correlation_id, details? }`.

## 2. Tables (spec §31 + additions)
§31 (all required): `users`, `brands`, `brand_profiles`, `audiences`, `offers`, `campaigns`, `source_assets`, `content_ideas`, `content_assets`, `content_versions`, `publication_jobs`, `publications`, `analytics_snapshots`, `experiments`, `experiment_variants`, `agent_runs`, `model_runs`, `prompt_versions`, `platform_accounts`, `platform_credentials`, `audit_events`.

Additions (justified by amendments / spec sections):
| Table | Why |
|---|---|
| `api_tokens` | Auth (ADR-0006): hashed bearer tokens per user. |
| `research_packets` | §7B / A5: one packet per campaign with provenance. |
| `research_claims` | A5: each claim labeled `SOURCE_STATED \| NEEDS_VERIFICATION \| OPINION`, optional citation (`url`, `retrieved_at`, `excerpt_hash`). |
| `campaign_strategies` | §7A: versioned structured strategy output per campaign. |
| `plan_items` | §7D: one row per scheduled item in the 7/14/30-day plan. |
| `qa_results` | §11: one row per (content_version, stage) with findings JSON. |
| `approval_actions` | §37: every approval action + actor. |
| `embeddings` | A4 / §10 / §24: pgvector storage, polymorphic owner, one partial HNSW index per embedding model (see DATA_MODEL). |
| `derived_metric_definitions` | §17: configurable formulas and weights. |
| `recommendations` | §19: optimization output, each with evidence snapshot IDs. |

## 3. Enums
- `Platform`: `LINKEDIN, X, INSTAGRAM, FACEBOOK, YOUTUBE, REDDIT, SUBSTACK, RUMBLE, MASTODON`. (YouTube Shorts = `YOUTUBE` + format `SHORT`.)
- `ContentFormat`: `TEXT_POST, THREAD, ARTICLE, CAROUSEL, DOCUMENT_CAROUSEL, POLL, REEL_SCRIPT, STORY, IMAGE_POST, LONG_VIDEO_OUTLINE, LONG_VIDEO_SCRIPT, SHORT, NEWSLETTER, NOTE, DISCUSSION, COMMENT_DRAFT`.
- `SourceType`: `TEXT, URL, MARKDOWN, PDF, DOCX, TRANSCRIPT, SUBTITLE, AUDIO, VIDEO, YOUTUBE_URL, NOTE`. (AUDIO/VIDEO: accepted, `NotImplemented` processing until later phase.)
- `CampaignObjective` (§39): `REACH, AUTHORITY, FOLLOWERS, EMAIL_SUBSCRIBERS, LEADS, PRODUCT_AWARENESS, SALES`.
- `FunnelStage`: `AWARENESS, CONSIDERATION, CONVERSION, RETENTION`.
- `AngleType` (§7C): `EDUCATIONAL, CONTRARIAN, STORY, TACTICAL, COMPARISON, OPINION, DATA_DRIVEN, CASE_STUDY`.
- `ClaimLabel` (A5): `SOURCE_STATED, NEEDS_VERIFICATION, OPINION`.
- `QaStage` (§11): `STRUCTURAL, BRAND, ACCURACY, PLATFORM`.
- `QaOutcome`: `PASS, WARN, FAIL`.
- `ApprovalActionType` (§37): `APPROVE, REJECT, REQUEST_REVISION, EDIT, REGENERATE, SCHEDULE, PUBLISH_NOW`.
- `ActorType`: `HUMAN, SYSTEM, SCHEDULER, AGENT`. Every transition records `actor_type` + `actor_id` (user id or agent name).
- `UserRole`: `OWNER, EDITOR, VIEWER`.
- `RoutingProfile` (§51): `LOCAL_ONLY, BALANCED, QUALITY_FIRST`.
- `ModelRole` (§3): `CHEAP_LOCAL, REASONING_LOCAL, CODING_LOCAL, EMBEDDING_LOCAL, FRONTIER_FAST, FRONTIER_REASONING, FRONTIER_CREATIVE`.
- `PrivacyClass` (§52): `PUBLIC, INTERNAL, PRIVATE`. `PRIVATE` never routes to a `REMOTE` model.
- `ModelLocality`: `LOCAL, REMOTE` (classified per model, not per provider — see PHASE0 R3).
- `PublishMode`: `MOCK, MANUAL, LIVE` (env `PUBLISH_MODE`; default `MOCK`).
- `PlatformCapability` status: `VERIFIED, UNVERIFIED, UNAVAILABLE`.
- `ErrorCode` (§29 + additions): `MODEL_TIMEOUT, MODEL_UNAVAILABLE, PROVIDER_RATE_LIMIT, LLM_OUTPUT_INVALID, BUDGET_EXCEEDED, PRIVACY_VIOLATION, PLATFORM_AUTH_FAILURE, PLATFORM_RATE_LIMIT, PLATFORM_TIMEOUT, PUBLICATION_UNCERTAIN, VALIDATION_FAILURE, INVALID_STATE_TRANSITION, SSRF_BLOCKED, INGESTION_FAILURE, RESEARCH_FAILURE, DATABASE_FAILURE, QUEUE_FAILURE, NOT_FOUND, CONFLICT, UNAUTHORIZED, FORBIDDEN, RATE_LIMITED`.

## 4. State enums (authoritative detail in STATE_MACHINES.md)
- `campaign_status`: `DRAFT, PLANNING, READY_FOR_REVIEW, ACTIVE, PAUSED, COMPLETED, ARCHIVED, FAILED`.
- `content_asset_status` (§11 chain + side states): `DRAFT, GENERATED, QA_FAILED, QA_PASSED, AWAITING_APPROVAL, REVISION_REQUESTED, APPROVED, REJECTED, SCHEDULED, PUBLISHED, ARCHIVED`.
- `publication_job_status` (outbox, A3): `PENDING, CLAIMED, SUCCEEDED, RETRY_WAIT, FAILED, UNCERTAIN, CANCELLED`.
- `publication_status` (the external post): `PUBLISHING, PUBLISHED, PUBLICATION_UNCERTAIN, FAILED, MANUAL_PENDING, DELETED`.
- `experiment_status`: `DRAFT, RUNNING, COLLECTING, CONCLUDED, INCONCLUSIVE, CANCELLED`.

## 4a. Reconciliation additions (accepted from Phase 1 subagent proposals)
- Enum type naming rule: GLOSSARY `PascalCase` enum → Postgres type `snake_case` (`Platform` → `platform`, `ErrorCode` → `error_code`).
- `MetricSource`: `MOCK, FIXTURE, API, MANUAL` (column `analytics_snapshots.source`; MOCK/FIXTURE excluded from real recommendations).
- `ContentVersionOrigin`: `GENERATED, HUMAN_EDIT, REGENERATION`.
- `ContentIdeaStatus`: `PROPOSED, SELECTED, DISCARDED`.
- `SourceAssetStatus`: `PENDING, NORMALIZED, FAILED, UNSUPPORTED`.
- `AgentRunStatus`: `RUNNING, SUCCEEDED, FAILED`; `ModelRunStatus`: `SUCCEEDED, FAILED` (detail in `error_code`).
- `RecommendationStatus`: `PROPOSED, ACCEPTED, DISMISSED`.
- `ExperimentVariable` (§18): `HOOK, TITLE, IMAGE, CTA, POST_LENGTH, POSTING_TIME, CAROUSEL_LENGTH, VIDEO_OPENING, THUMBNAIL, TOPIC`.
- `CredentialKind`: `OAUTH2, API_KEY, APP_PASSWORD`.
- `EmbeddingOwnerType` (native enum `embedding_owner_type`, Phase 2): `CONTENT_VERSION, SOURCE_ASSET, RESEARCH_CLAIM, BRAND_PROFILE, AUDIENCE, OFFER, CAMPAIGN_STRATEGY, CONTENT_IDEA`; `EmbeddingField`: `FULL, HOOK, CTA, CHUNK`.
- `ContentFormat` += `QUOTE_POST` (X quote-post drafts, §8).
- `PlatformAccountType`: `PERSONAL, ORGANIZATION, PAGE, CHANNEL, SUBREDDIT_POSTER, INSTANCE_USER` (column `platform_accounts.account_type`).
- Publisher class names (spec §15, A6): `PlatformPublisher` (interface), `MockPublisher`, `ManualExportPublisher`, `<Platform>LivePublisher` (Phase 13).
- Credential master key setting: `OMNIGROWTH_CREDENTIAL_KEK` (+ `kek_id` on each row).
- Embeddings: **one `vector` column + one partial HNSW index per registered embedding model** (supersedes §2's "fixed-dimension column per model" wording).
- Table `campaign_sources` (campaign ↔ source_asset, many-to-many; replaces a single `campaigns.source_asset_id`).
- `PUBLISH_MODE` env parsing is case-insensitive (`live` → `PublishMode.LIVE`).
- Status-writing rule scope: the four machines in STATE_MACHINES.md plus the small `jobs` and `source_asset` (Phase 4, STATE_MACHINES section 7) machines; all live in `omnigrowth/core/state_machines/`. Exception (Phase 3): `model_runs` is an immutable append-only log whose `status` is the outcome written once at insert by `omnigrowth/storage/model_runs.py` (never updated); the same applies to `agent_runs` outcome rows when implemented.
- Tables added from API_PLAN: `jobs` (durable async-job record), `idempotency_keys` (request-level), `app_settings` (DB tunables + routing overrides); `webhook_endpoints`, `webhook_subscriptions` (Phase 12).
- `JobStatus`: `QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED`. `JobKind`: `SOURCE_INGEST, CAMPAIGN_PLANNING, ASSET_GENERATION, CONTENT_REGENERATION, QA_RUN, EMBEDDING, REEMBED, ANALYTICS_PULL, EXPERIMENT_EVALUATION, RECOMMENDATION_GENERATION, PLATFORM_VERIFY, PUBLICATION_RECONCILE, WEBHOOK_DISPATCH`.
- `ErrorCode` += `LIVE_PUBLISH_BLOCKED` (A6 guard refusal; `details.guard ∈ {ENV_TEST, ACCOUNT_NOT_LIVE_ENABLED, VERSION_NOT_APPROVED, OPEN_UNCERTAIN}`, plus live-enable refusals `CONFIRMATION_MISMATCH, NO_CREDENTIALS, CAPABILITY_UNVERIFIED`), `INTERNAL_ERROR` (uncategorized 500; still logged with context).
- `ReconcileMethod`: `FETCH_BY_EXTERNAL_ID, MANUAL_CONFIRM_PUBLISHED, MANUAL_CONFIRM_NOT_PUBLISHED`. `CalendarItemKind`: `PUBLICATION_JOB, PLAN_ITEM`. `HealthStatus`: `OK, DEGRADED, DOWN`. `MetricNullReason`: `MISSING_INPUT, ZERO_DENOMINATOR, BELOW_MIN_DENOMINATOR`.
- `idempotency_keys.principal_type`: `USER, WEBHOOK_ENDPOINT`; `idempotency_keys.state`: `IN_PROGRESS, COMPLETED` (text + CHECK). `RoutingProfileRead.source`: `GLOBAL, BRAND, CAMPAIGN`. Audit metadata key `retry_of_job_id` links a manual-retry job to its FAILED/CANCELLED predecessor.
- Additional request schemas: `PublicationJobRetryRequest`, `ManualNotPublishedRequest`, `PublicationMarkDeletedRequest`.
- Phase 2 additions: `ErrorCode` += `CONFIGURATION_ERROR` (invalid settings/config files, HTTP 500). `Environment`: `dev, test, prod` (env `ENV`). Runtime setting `OMNIGROWTH_RUNTIME`: `host | docker` (selects preset `base_url_host` / `base_url_docker`, A1).
- Extra platform-specific metrics that have no AnalyticsSnapshot column (dwell proxies, open rate, upvote ratio) go in `analytics_snapshots.platform_metrics JSONB`.

## 5. Services / components (names used in ARCHITECTURE.md)
`api` (FastAPI), `worker` (RQ), `scheduler` (outbox loop), `postgres` (+pgvector), `redis`. Python packages follow spec §4 under `omnigrowth/`, plus `omnigrowth/core/` (settings, errors, logging, state machines).
