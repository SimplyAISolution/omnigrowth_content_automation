# ADR-0004 — pgvector for embeddings

- Status: ACCEPTED (2026-09-23, human "proceed" at Phase 1 gate)
- Date: 2026-09-22
- Spec: §10, §24 · Amendments: A4

## Decision
- Embeddings live in PostgreSQL via the pgvector extension in the same instance (image `pgvector/pgvector:pg16`). No separate vector database.
- The embedding model is the `EMBEDDING_LOCAL` router role; each row stores `embedding_model` and `dim`. Fixed-dimension index layout is specified in DATA_MODEL.md (`embeddings` table).
- Changing the embedding model triggers a re-embed job; similarity queries always filter by `embedding_model`, so vectors from different models are never compared.
- Candidate local models (PHASE0 L159, L171, L173): `text-embedding-nomic-embed-text-v1.5` (LM Studio), `nomic-embed-text`, `qwen3-embedding` (Ollama). Dimension is measured in Phase 3, not assumed.
- Duplicate/fatigue detection (§10) is warn-only with a configurable cosine threshold.

## Consequences
+ Transactions and joins across content and vectors; one backup.
− HNSW indexes need a fixed dimension, so the schema must accommodate model changes (see DATA_MODEL).

## Alternatives
Qdrant / Chroma sidecar: better at very large scale; unnecessary for a per-brand content corpus and adds a cross-store consistency problem.
