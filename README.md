# OmniGrowth

Local-first, multi-platform content operating system (FastAPI + PostgreSQL/pgvector + Redis/RQ + local OpenAI-compatible LLM).

Status: **Phase 2 — skeleton + core backend.** See `docs/STATUS.md` for the current phase and `docs/PHASES.md` for the plan.
Full installation instructions (PowerShell and bash) are written in the documentation phase; until then see `DEVELOPMENT` notes below.

## Quick start (development)

PowerShell:
```powershell
uv sync
Copy-Item .env.example .env   # then edit secrets
docker compose -p omnigrowth up -d --wait
uv run poe migrate
uv run poe check
```

bash:
```bash
uv sync
cp .env.example .env   # then edit secrets
docker compose -p omnigrowth up -d --wait
uv run poe migrate
uv run poe check
```

Architecture: `ARCHITECTURE.md`. Decisions: `docs/adr/`.
