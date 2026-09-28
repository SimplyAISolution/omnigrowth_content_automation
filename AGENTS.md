# AGENTS.md — OmniGrowth (binding for the primary agent and all subagents)

This file is injected into every turn. It holds rules only. The spec lives in `docs/`.

## 1. Precedence (highest wins; log any conflict in ENGINEERING_LOG.md)
1. `AGENTS.md` (this file)
2. `docs/SPEC_AMENDMENTS.md`
3. `docs/PHASES.md`
4. Accepted ADRs in `docs/adr/`
5. `docs/spec/OMNIGROWTH_SPEC.md` (original requirements)

## 2. Session-start protocol (every new session or after /compress)
1. Read `docs/STATUS.md` → current phase, current task, blockers.
2. Read the last entry of `ENGINEERING_LOG.md`.
3. Run `git status` and `git log --oneline -10`.
4. Resume the current task. Never re-plan or rebuild a phase marked GATE_PASSED.

## 3. Hard prohibitions
- No `git push`, force-push, rebase of existing history, or commits containing secrets.
- No system-level installs (winget, choco, global npm/pip, registry, PATH edits) without asking.
- No network calls to social platforms. No setting `PUBLISH_MODE=live`. Ever, in any phase, unless the human types the exact phrase `ENABLE LIVE PUBLISHING FOR <platform>`.
- No hard-coded model names, endpoints, or credentials in application code. Config/env only.
- No `except Exception: pass`, bare `except:`, or swallowed errors. Catch → raise a categorized `OmniGrowthError` (see `omnigrowth/core/errors.py`) or log with context and re-raise.
- No executing model-generated code or shell strings.
- No deleting or overwriting files you did not create in this repo without reading them first and logging why.

## 4. Engineering rules
- Tooling is pinned: `uv` for env/deps, `poe` tasks in `pyproject.toml` as the single command surface (identical in PowerShell and bash).
- Canonical gate: `uv run poe check` = ruff format --check + ruff lint + mypy (strict on `omnigrowth/`) + import contracts + pytest excluding `live_llm` and `integration` markers + `poe ui-check` (frontend typecheck + component tests).
- Browser E2E (`uv run poe ui-e2e`) is a separate command, required at a phase gate whenever the phase changes frontend journeys, authentication, authorization or API contracts used by the UI.
- Tests never hit a real LLM or the internet by default. Use `FakeProvider` fixtures (recorded JSON). Live tests are opt-in: `-m live_llm`.
- LLM structured output: JSON schema request → Pydantic validation → max 2 repair attempts → `LLM_OUTPUT_INVALID` failure with the raw payload persisted to `model_runs`.
- Alembic: ONLY the primary agent creates revisions. `uv run alembic heads` must always print exactly one head.
- Every state transition on Campaign/ContentAsset/Publication goes through the state-machine module; no direct `status =` assignments elsewhere (enforced by a test).
- Prompts live in `omnigrowth/prompts/<domain>/<name>.v<N>.md` with front-matter. Never inline prompts >5 lines in Python.

## 5. Delegation rules (delegate_task / reviewer)
- Delegate only leaf tasks with DISJOINT file ownership. Every brief must state:
  `OWNS:` files it may create/modify · `READS:` read-only files · `GATE:` command that must pass · `RETURN:` summary format (files changed, test output tail, open issues).
- Never delegate in parallel anything touching: `pyproject.toml`, `uv.lock`, `alembic/`, `omnigrowth/core/`, `omnigrowth/schemas/common.py`, `docker-compose.yml`, `AGENTS.md`, `docs/STATUS.md`.
- After any parallel batch, the primary agent runs `uv run poe check` before committing.
- At each phase gate, dispatch a reviewer subagent with the phase's acceptance criteria from `docs/PHASES.md`.

## 6. Honesty rules
- A task is DONE only when its gate command ran in this session and passed. Paste the last ≤20 lines of real output into ENGINEERING_LOG.md.
- If a gate was not run, say `NOT RUN` — never infer a pass.
- Unknown platform API capability → document as UNVERIFIED in `docs/PLATFORM_CAPABILITIES.md`; do not implement against guessed endpoints.

## 7. Stop and ask the human when
- A phase gate is reached (always stop; report; wait).
- An action is destructive, costs money, or needs a credential.
- Two consecutive fix attempts for the same root cause failed.
- A spec ambiguity would change a DB schema, public API contract, or state machine.

## 8. Phase report format (append to ENGINEERING_LOG.md and print to chat)
```
PHASE <n> — <name> — <GATE_PASSED | BLOCKED>
COMPLETED:
TESTS: <passed>/<failed>/<skipped> (command + output tail)
FILES CHANGED:
KNOWN ISSUES:
DECISIONS NEEDED:
NEXT PHASE:
```
