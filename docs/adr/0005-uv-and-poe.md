# ADR-0005 — uv + poethepoet as the single command surface

- Status: ACCEPTED (2026-09-23, human "proceed" at Phase 1 gate)
- Date: 2026-09-22
- Spec: §40, §42, §43 · Amendments: A10

## Decision
- `uv` manages Python, the virtualenv (`.venv`), and the lockfile (`uv.lock`). uv 0.8.17 is installed (PHASE0 L30). No global pip installs.
- `poethepoet` tasks in `pyproject.toml` are the "Makefile or equivalent" (§40). The same command works in PowerShell, cmd and bash: `uv run poe <task>`.
- Planned tasks: `check` (ruff format --check, ruff lint, mypy strict on `omnigrowth/`, **import-linter — an addition to the AGENTS.md §4 canonical gate; accepting this ADR approves that change**, pytest excluding `live_llm` and `integration`), `fmt`, `lint`, `type`, `test`, `test-int`, `test-live`, `e2e`, `security` (bandit + pip-audit), `migrate`, `revision`, `api`, `worker`, `worker-win`, `scheduler`, `create-owner`, `up`, `down`.
- Tools: ruff (format + lint; replaces Black), mypy, pytest + pytest-asyncio, bandit, pip-audit, import-linter.
- Docker operations that cannot be made shell-agnostic get `scripts/*.ps1` + `scripts/*.sh` pairs.

## Consequences
+ One documented entry point on a Windows host; no Make dependency.
− Contributors need uv. Mitigation: README install steps in PowerShell and bash.

## Alternatives
Make (not native on Windows); nox/tox (heavier env-per-session model).
