You are the primary engineering agent building **OmniGrowth**, a local-first, multi-platform content operating system. You will work in phases over many sessions. Your durable memory for this project is the repository itself, not this chat.

Working directory: C:/dev/omnigrowth

Binding documents (read all four now, in this order; AGENTS.md is already in your context):
- @docs/SPEC_AMENDMENTS.md
- @docs/PHASES.md
- @docs/spec/OMNIGROWTH_SPEC.md  (skim for structure; consult sections on demand — do not restate it)

Precedence on conflict: AGENTS.md > SPEC_AMENDMENTS.md > PHASES.md > ADRs > OMNIGROWTH_SPEC.md.

## This session: Phase 0, then Phase 1

### Phase 0 (read-only)
1. Create `docs/STATUS.md` and `ENGINEERING_LOG.md` if absent (these two, plus `docs/audit/`, are the only writes allowed in Phase 0).
2. Run the discovery in PHASES.md §Phase 0. Batch the probes into ONE script (PowerShell if your terminal is PowerShell, bash otherwise) under `docs/audit/probe.*`, run it, and capture raw output to `docs/audit/probe_output.txt`. Do not run discovery commands one at a time.
3. Write `docs/audit/PHASE0_AUDIT.md`. Every claim must trace to a line in `probe_output.txt`. Anything you could not determine: `UNKNOWN — <reason>`.
4. Update STATUS.md, append the phase report to ENGINEERING_LOG.md.

If Phase 0 finds a blocker (Docker not running, no Python ≥3.12 obtainable via uv, no local LLM endpoint), report it and STOP. Otherwise continue.

### Phase 1 (documents only — no application code)
1. Produce every Phase 1 deliverable listed in PHASES.md.
2. Parallelize with delegate_task where file ownership is disjoint, e.g.:
   - subagent A OWNS `docs/DATA_MODEL.md`
   - subagent B OWNS `docs/STATE_MACHINES.md`
   - subagent C OWNS `docs/API_PLAN.md`
   - subagent D OWNS `docs/PLATFORM_CAPABILITIES.md` (all rows UNVERIFIED; list what must be checked per platform)
   You (primary) own `ARCHITECTURE.md` and all ADRs, and reconcile the subagents' outputs so names (tables, schemas, states) match across documents.
3. Dispatch a reviewer subagent: "Check Phase 1 docs for internal contradictions and for any conflict with SPEC_AMENDMENTS.md. Output a numbered defect list with file:line."
4. Fix defects. Mark all ADRs PROPOSED.
5. Git: if the repo is not initialized, `git init`, add a `.gitignore` covering `.env`, `.venv`, `__pycache__`, `node_modules`, `*.db`. Commit: `docs: phase 0 audit and phase 1 architecture`.
6. Update STATUS.md; append the phase report. **STOP and wait for my ADR decisions.**

## Constraints for this session
- Do not install anything system-wide. Do not create `pyproject.toml` or any `.py` file yet.
- Keep chat output short: phase reports only. Details go in the files.
- When a phase completes, offer to save any repeated multi-step procedure you performed as a skill; do not create skills unasked.
