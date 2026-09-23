Resume OmniGrowth. Run the AGENTS.md §2 session-start protocol first and print a 3-line status (phase, last green commit, blockers).

Execute **Phase <N> — <NAME>** exactly as defined in @docs/PHASES.md. Relevant spec sections: <§…>. Relevant amendments: <A…>.

<!-- Paste only when needed: decisions from the previous gate -->
Decisions from last gate:
- ADR-000X: ACCEPTED | changed to: …
- …

Execution plan requirements (print it once, ≤15 lines, then execute without further narration):
1. Ordered task list. Mark each task P (primary) or D (delegable) per AGENTS.md §5.
2. For each D task, the delegate_task brief: OWNS / READS / GATE / RETURN.
3. The phase gate commands you will run at the end.

Rules for this phase:
- Commit after each task whose gate passes (`feat:`, `test:`, `fix:`, `docs:` prefixes). Never commit red.
- On any failure use the `omnigrowth-debug-loop` skill. Two failed fixes on one root cause → stop and report.
- At the end, load the `omnigrowth-phase-gate` skill and follow it. Then STOP.
