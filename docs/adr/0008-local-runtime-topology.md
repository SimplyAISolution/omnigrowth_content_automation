# ADR-0008 — Local runtime topology on this workstation

- Status: ACCEPTED (2026-09-23, human "proceed" at Phase 1 gate)
- Date: 2026-09-22
- Spec: §41, §42 · Amendments: A1, A10

## Context
Phase 0 found host ports 5432 and 6379 already bound by another project's containers (PHASE0 L78–L79, L180–L181), other agent containers on the same engine (L74–L76), and the repo on NTFS with Docker Desktop's WSL2 backend (L3, L58).

## Decision
- Compose project name fixed to `omnigrowth` (`name:` key in `docker-compose.yml`); scripts pass `-p omnigrowth`.
- Host ports: api 8000, postgres **55432**, redis **56379** (overridable in `.env`). Containers use service DNS (`postgres:5432`, `redis:6379`).
- `DATABASE_URL` / `REDIS_URL` are always explicit; no library defaults. This prevents silently connecting to the other project's database on 5432.
- Containers reach host LLMs via `host.docker.internal` (+ `extra_hosts: host-gateway`).
- Named volumes for Postgres/Redis data. Source bind-mount only for `api` dev hot-reload; reload latency measured in Phase 2 and recorded (A10). The repo is not moved without asking.

## Consequences
+ Coexists with the existing `decentrlai-*` and `hermes-*` containers.
− Non-default ports must be documented in README / DEPLOYMENT with PowerShell and bash examples.
