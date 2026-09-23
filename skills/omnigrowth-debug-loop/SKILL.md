---
name: omnigrowth-debug-loop
description: Root-cause debugging procedure for OmniGrowth (spec §47). Use whenever a test, gate command, container, or runtime call fails. Prevents blind rewrites.
---

# OmniGrowth debug loop

## Procedure
1. **Reproduce** with the narrowest command (`uv run pytest path::test -x -q`, one container's logs, one request). Record the exact command.
2. **Capture** the exact error: exception type, message, top relevant frame; for containers `docker compose logs --tail=80 <svc>`.
3. **Classify** against `omnigrowth/core/errors.py` categories (or ENV/TOOLING if outside the app). If none fits, that is itself a finding.
4. **Hypothesize** one root cause in one sentence, citing the evidence line.
5. **Smallest fix**: minimum change addressing that cause. No refactors, renames, or dependency bumps unless the dependency is the cause.
6. **Verify**: re-run the reproducing command → the relevant test module → `uv run poe check`.
7. **Log** one ENGINEERING_LOG.md entry: symptom → cause → fix → verification output tail.

## Escalation
- Attempt counter is per root cause. After 2 failed fixes: revert only the files you changed in this loop, write findings, STOP and ask the human.
- Environmental causes (Docker Desktop down, port conflict, VRAM exhaustion, local model not loaded): do not patch application code around them. Report.

## OmniGrowth-specific causes to check first
- Two Alembic heads after parallel work → merge revision, primary agent only.
- Container can't reach local LLM → `127.0.0.1` used instead of `host.docker.internal`.
- Flaky LLM tests → test is hitting a real provider; must use FakeProvider fixtures.
- Windows path/line-ending issues in fixtures → explicit `encoding="utf-8"`, `newline=""`.
