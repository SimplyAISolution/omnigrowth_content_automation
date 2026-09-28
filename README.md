# OmniGrowth

Local-first, multi-platform content creation and automation for individual influencers, companies, and groups. OmniGrowth supports brainstorming, strategic planning, content creation, review, scheduling, and publishing workflows designed to improve reach and engagement in targeted niches.

Built with FastAPI, PostgreSQL/pgvector, Redis/RQ, and local LLM inference through LM Studio.

## Project status

See [Development Status](docs/STATUS.md) for current progress and [Development Phases](docs/PHASES.md) for the roadmap.

Real-platform publishing requires connector configuration and explicit activation. Mock publication does not send content to a real platform.

## Quick start for development

Configure secrets locally in `.env`. Never commit that file.

### PowerShell

```powershell
uv sync
Copy-Item .env.example .env
# Edit .env before continuing.
docker compose -p omnigrowth up -d --wait
uv run poe migrate
uv run poe check
```

### Bash

```bash
uv sync
cp .env.example .env
# Edit .env before continuing.
docker compose -p omnigrowth up -d --wait
uv run poe migrate
uv run poe check
```

These commands prepare the development environment and run checks. Consult the project documentation for application startup and connector configuration.

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Current status](docs/STATUS.md)
- [Development roadmap](docs/PHASES.md)
- [Architecture decisions](docs/adr/)