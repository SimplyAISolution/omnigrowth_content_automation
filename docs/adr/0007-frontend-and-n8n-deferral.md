# ADR-0007 — Defer frontend and n8n until after the MVP gate

- Status: ACCEPTED (2026-09-23, human "proceed" at Phase 1 gate)
- Date: 2026-09-22
- Spec: §3, §12, §13, §41, §56 · Amendments: A7

## Decision
- The MVP (Phase 7 gate) is API-only. §56 ranks UX and visual polish last.
- Frontend (Phase 11): Vite + React + TypeScript, API client generated from OpenAPI, no business logic in the client. Views in priority order: Approvals → Content Queue → Calendar → Campaigns → Analytics → rest.
- n8n (Phase 12): not in compose until then. The backend exposes HMAC-verified webhooks and emits outbound events so n8n can be added without backend changes. Business logic never lives in n8n.
- Consequence for §41: compose ships `api`, `worker`, `scheduler`, `postgres`, `redis` now; `frontend` and `n8n` are added in their phases.

## Consequences
+ Effort goes to correctness, security and the E2E workflow first.
− No visual review surface until Phase 11; review happens through the API and FastAPI's `/docs`.

## Alternatives
Next.js (spec: "React or Next.js"): SSR is unnecessary for a local dashboard; a Vite SPA is simpler to serve and containerize.
