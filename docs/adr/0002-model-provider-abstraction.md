# ADR-0002 — Model provider abstraction and privacy-aware routing

- Status: ACCEPTED (2026-09-23, human "proceed" at Phase 1 gate)
- Date: 2026-09-22
- Spec: §3, §23, §35, §51, §52 · Amendments: A1, A2, A8

## Context
LM Studio (:1234) and Ollama (:11434) both respond on this host (PHASE0_AUDIT L150, L163). Ollama lists `deepseek-v3.1:671b-cloud` (L165), which by its name appears to be a remote model behind a local endpoint. The user environment also contains `OPENAI_API_KEY` and `ANTHROPIC_BASE_URL` (L187, L192).

## Decision
1. One provider class `OpenAICompatibleProvider(base_url, api_key?, capabilities)`; presets `lmstudio` and `ollama` (A1). Base URL resolution: `OMNIGROWTH_LOCAL_LLM_URL` > preset > error.
2. `config/models.yaml` maps `ModelRole` → ordered candidate list of `{provider, model, locality}`. No model names in Python.
3. **Locality is declared per model.** Any model matching `remote_patterns` (default `["*-cloud", "*:cloud"]`) is forced to `REMOTE`, regardless of provider.
4. Every task carries a `PrivacyClass`. `PRIVATE` tasks may only use `LOCAL` models, otherwise `PRIVACY_VIOLATION`. Default class for anything containing unpublished source material or brand data: `PRIVATE`.
5. Routing profiles `LOCAL_ONLY` (default, used by all tests) / `BALANCED` / `QUALITY_FIRST` filter candidates; a budget check precedes any remote call.
6. One cloud adapter (OpenAI-compatible **or** Anthropic, config-selected), enabled only when `OMNIGROWTH_CLOUD_API_KEY` is set. Base URL comes from OmniGrowth config only; generic SDK env vars are ignored.
7. Per-provider concurrency semaphore, default 1 (A2).
8. `FakeProvider` replays fixtures keyed by `(prompt_id, prompt_version, input_hash)`; missing fixture = test failure (A8).
9. Structured-output capability (json_schema / json_object / prompt-only) is probed at health-check time and cached per model.

## Consequences
+ Private material cannot leak to a cloud model through a "local" endpoint misconfiguration.
+ Tests are deterministic and offline.
− Pattern-based locality is a heuristic; the human must still review `models.yaml`. The health check prints each model's locality.

## Alternatives
- LiteLLM as router: large dependency surface; hides the privacy decision inside a third-party library.
- Separate `LMStudioProvider` / `OllamaProvider` classes: duplicate code for identical APIs (A1 forbids).
