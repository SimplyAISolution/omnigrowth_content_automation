# ADR-0003 — Redis + RQ for async work; Postgres outbox for publication

- Status: ACCEPTED (2026-09-23, human "proceed" at Phase 1 gate)
- Date: 2026-09-22
- Spec: §14, §30 · Amendments: A3

## Decision
- Async work (ingestion, agents, generation, QA, embeddings, analytics pulls) → **Redis + RQ**.
- Publication scheduling → **Postgres transactional outbox** (`publication_jobs`), claimed by a dedicated `scheduler` process with `SELECT … FOR UPDATE SKIP LOCKED` and a lease. Overdue rows are simply due rows (missed-job recovery).
- `idempotency_key = sha256(content_version_id + platform_account_id)`, unique index. Canonical input: `lower(str(content_version_id)) + ":" + lower(str(platform_account_id))`, UTF-8, hex digest (DATA_MODEL OQ-3). The unique index is **partial**, over `status IN ('PENDING','CLAIMED','RETRY_WAIT','SUCCEEDED','UNCERTAIN')`: at most one live-or-succeeded job per (version, account). `FAILED` and `CANCELLED` are proven-not-published terminal rows kept as history; a manual retry inserts a new row. This narrows A3's "unique index" wording without weakening duplicate-post prevention — accepted at the Phase 1 gate.
- Dispatch is fenced: `claim_token` on claim, `dispatch_started_at` committed before any network call. Expired lease with no dispatch → back to `PENDING`; with dispatch → `UNCERTAIN`. Unknown outcome → `UNCERTAIN` / `PUBLICATION_UNCERTAIN`, reconciled by platform-ID lookup or human confirmation; never auto-retried.
- Retries with exponential backoff + jitter only for errors that provably did not reach the platform.

## Windows note (important)
RQ's default worker uses `fork()`, which **does not exist on native Windows**. Workers therefore run in Linux containers (compose `worker` service) or WSL. For native-Windows debugging, `poe worker-win` runs `rq worker --worker-class rq.SimpleWorker` (no fork; one job at a time). Tests run jobs synchronously and need no worker.

## Consequences
+ Losing Redis loses only re-runnable work; it can never duplicate or drop a publication.
+ The duplicate-publish race is testable at the DB level (two schedulers → one publication).
− Two mechanisms to operate (Redis queue + DB outbox).
− RQ lacks Celery-style chords; campaign pipelines are orchestrated by a `workflows/` job that enqueues the next step on success.

## Alternatives
- **Dramatiq** (+Redis): runs natively on Windows, built-in retries/middleware. Strong alternative if native-Windows workers matter.
- Celery: most features, highest operational surface.
- Postgres-only queue (e.g. procrastinate): one fewer service; smaller ecosystem.
