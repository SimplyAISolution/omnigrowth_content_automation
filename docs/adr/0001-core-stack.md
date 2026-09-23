# ADR-0001 — Core stack

- Status: ACCEPTED (2026-09-23, human "proceed" at Phase 1 gate)
- Date: 2026-09-22
- Spec: §3, §30, §31, §50 · Amendments: A7

## Context
The spec prescribes Python 3.12+, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL. Open choices: sync vs async database access, and the Python version to target. Phase 0 found Python 3.13.6 (default) and 3.12.0 via uv (PHASE0_AUDIT L27–L33).

## Decision
- Python `>=3.12`; dev default 3.13 (uv-managed); CI matrix 3.12 + 3.13.
- FastAPI + Pydantic v2 + pydantic-settings.
- SQLAlchemy 2.0 **async** ORM with the **psycopg 3** driver (one driver covers sync and async, so Alembic and scripts can use sync mode with the same URL scheme).
- Services are `async`. RQ job entrypoints are thin sync wrappers that call `asyncio.run(service(...))`.
- PostgreSQL 16 with the pgvector extension (ADR-0004). Alembic is the only schema-creation path; `create_all` is forbidden outside tests.
- HTTP client: httpx (async). Structured logs: stdlib `logging` + JSON formatter (no extra framework).

## Consequences
+ One driver, one ORM style, async I/O where §50 asks for it.
+ Worker code reuses the same async services the API uses.
− `asyncio.run` per job adds small overhead and prevents sharing an event loop across jobs (acceptable; jobs are coarse).
− Async SQLAlchemy has sharper edges (lazy loading raises). Mitigation: `lazy="raise"` on relationships, explicit `selectinload`.

## Alternatives
- Sync SQLAlchemy everywhere: simpler; gives up async I/O in the API. Viable if the human prefers simplicity over §50.
- SQLModel: blurs API DTOs and ORM models, which ARCHITECTURE §3 keeps separate.
