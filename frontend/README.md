# OmniGrowth frontend (Phase 11)

Vite + React + TypeScript single-page app over `/api/v1` (ADR-0007). It holds no business
rules: the API's state machines and roles decide what is allowed, and the UI shows the API's
answer. Hiding or disabling a control is only a hint; every request is authorised by the API.

## Run it

```bash
npm --prefix frontend install          # project-local dependencies only
npm --prefix frontend run dev          # http://127.0.0.1:5173, proxies /api to 127.0.0.1:8000
```

Point it at another API with `OG_API_URL` (for example the throwaway environment below):
`OG_API_URL=http://127.0.0.1:8012 npm --prefix frontend run dev`.

The API sends no CORS headers and never uses cookies, so the dev and preview servers proxy
`/api` same-origin. Sign-in stores the bearer token in `sessionStorage` (per tab).

## Checks

| Command | What it runs |
|---|---|
| `uv run poe ui-check` | `tsc` typecheck + Vitest component tests (mocked API) |
| `uv run poe ui-e2e` | Playwright journeys in the installed Microsoft Edge against the **real API** |
| `uv run poe openapi` then `npm --prefix frontend run gen:api` | refresh the OpenAPI snapshot and the generated client |

`tests/unit/test_openapi_snapshot.py` fails when `src/api/openapi.json` no longer matches the
API, so contract changes are explicit.

### Browser tests and demo data

`uv run poe ui-e2e` first runs `scripts/ui_env.py prepare`, which creates a throwaway database
`og_ui_<hex>`, migrates it and seeds it through the API with **fixture replay** (hand-written
canonical model answers; no real LLM): a demo brand, mock platform accounts, one campaign
published through the **mock publisher** (simulated metrics and suggestions) and one waiting
for approval. Users get freshly generated test passwords, written only to the git-ignored
`.pytest-tmp/ui-env.json` (outside every served directory). The API runs on port 8012 with `PUBLISH_MODE=MOCK`, Redis DB 15 and an
in-process rate limiter; the database is dropped afterwards (`OG_UI_KEEP=1` keeps it).
Screenshots land in `test-results/screens/` (git-ignored).

## What is simulated, and what is not available

- Publishing everywhere is the mock publisher: no platform call, no real audience. Every
  published state, job and metric from it is labelled "simulated".
- Supported formats come from `GET /api/v1/platforms` (backed by `config/qa.yaml`); limits
  there are UNVERIFIED.
- No API yet (explained in the UI): pausing, duplicating or archiving campaigns; model and
  routing settings; users and settings; automations (Phase 12); creating experiments and
  editing brand profiles from the UI; file/URL source upload (text only in the UI).
