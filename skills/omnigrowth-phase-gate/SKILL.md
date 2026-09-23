---
name: omnigrowth-phase-gate
description: Close out an OmniGrowth build phase — run gate commands, dispatch reviewer, record real test output, update STATUS and ENGINEERING_LOG, commit, stop. Use at the end of every phase defined in docs/PHASES.md.
---

# OmniGrowth phase gate

## Procedure
1. Read the target phase's **Gate** line in `docs/PHASES.md`. Turn it into an explicit checklist.
2. Run `uv run poe check` (skip only in Phases 0–1, where no code exists). Capture the final ≤20 lines verbatim.
3. Run every additional gate command for the phase. Capture each output tail.
4. Run `uv run alembic heads` (Phase 2+). Exactly one head, or the gate fails.
5. `git status --porcelain` must show no stray source files and no `.env` / secrets staged.
6. Dispatch a reviewer subagent with this brief:
   - OWNS: nothing (read-only)
   - READS: changed files (`git diff --stat <last_green_commit>..HEAD`), `docs/PHASES.md`, `docs/SPEC_AMENDMENTS.md`
   - TASK: verify each acceptance criterion by executing tests or reading code; flag swallowed exceptions, hard-coded models/URLs/secrets, direct status assignments, prompts inlined in Python, missing failure tests.
   - RETURN: numbered defects with file:line and severity (BLOCKER/MAJOR/MINOR).
7. Fix BLOCKER and MAJOR defects, re-run steps 2–4. List MINORs under KNOWN ISSUES.
8. Update the `docs/STATUS.md` header:
   ```
   phase: <n>
   gate: GATE_PASSED | BLOCKED
   last_green_commit: <sha>
   next_phase: <n+1>
   ```
9. Append the AGENTS.md §8 report to `ENGINEERING_LOG.md`, including raw output tails.
10. Commit `chore(gate): phase <n> passed` (no commit if BLOCKED).
11. Print the report to chat and STOP. Do not start the next phase.

## Failure modes to avoid
- Reporting counts from memory instead of captured output.
- Treating skipped tests as passed — report them separately.
- Letting the reviewer fix code (it is read-only; the primary fixes).
