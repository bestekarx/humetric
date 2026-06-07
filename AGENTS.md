# HuMetric — Agent Context

Structured context for AI coding agents (Claude Code, OpenAI Codex, GitHub Copilot Workspace, Gemini Code Assist, and similar tools).

## Project identity

HuMetric is a Python/FastAPI open-source entity intelligence platform. It ingests free-text "signals" about entities (workers, customers, dealers, vehicles, regions, etc.), extracts structured metrics via a multi-agent LLM pipeline, stores them with temporal decay, and exposes them via a REST API with hybrid vector+full-text search.

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Framework | FastAPI (async) + Uvicorn |
| Database | PostgreSQL 15 + pgvector + Row-Level Security |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| LLM | Anthropic Claude (Haiku for extraction/wizard, Sonnet for curation/ranking) |
| Embeddings | Voyage AI / OpenAI / Cohere (abstracted, switchable) |
| Queue | PostgreSQL (`SELECT FOR UPDATE SKIP LOCKED`) |
| Auth | Bearer API key, SHA-256 hashed |
| Build | `hatchling`, src layout (`src/humetric/`) |

## Codebase map

```
src/humetric/
  api.py           — All FastAPI route handlers (single file)
  store.py         — Async data-access layer (SQLAlchemy 2.0)
  worker.py        — Background task processor (signal pipeline loop)
  schema.py        — All Pydantic v2 request/response models
  config.py        — All settings loaded from environment variables
  auth.py          — API key hashing, bearer verification
  kvkk.py          — GDPR/KVKK consent enforcement
  embeddings.py    — Multi-provider embedding abstraction
  rag.py           — Hybrid retrieval (vector similarity + FTS)
  decay.py         — Temporal decay weighting
  agents/
    __init__.py    — Prompt loader (_load_prompt)
    base.py        — structured_call() — Anthropic client wrapper + retry
    extractor.py   — extract_metrics(signal_text, pack, tenant_id)
    curator.py     — curate_metrics(extracted, history, pack, tenant_id)
    ranker.py      — rerank(query, candidates, tenant_id)
    wizard.py      — generate_pack(description, tenant_id)
  db/
    database.py    — Async engine, session factories, RLS context helpers
    models.py      — SQLAlchemy ORM (Tenant, ApiKey, Entity, Signal, Task, ...)
  middleware/
    auth.py        — AuthMiddleware (API key → tenant_id resolution)
    rate_limit.py  — RateLimitMiddleware
    metrics.py     — PrometheusMiddleware
    billing_guard.py — Tier limit enforcement
  services/
    stripe_service.py  — Billing, checkout, webhooks
    email_service.py   — Transactional email
    captcha_service.py — Registration captcha verification
    usage_service.py   — Metering: signals, embeddings, LLM tokens
prompts/               — Externalized LLM prompt files (*.md)
packs/                 — Metric Pack YAML definitions
alembic/versions/      — Database migration scripts
tests/                 — pytest suite (gitignored — local dev only)
```

## Conventions agents must follow

### Async-first
All database calls, agent calls, and I/O are `async`. Never introduce synchronous blocking calls (`requests.get`, synchronous SQLAlchemy sessions) into async code paths.

### Pydantic v2 only
Use `model_config = ConfigDict(...)`, `@model_validator(mode="after")`, `@field_validator`. Do not use v1 patterns (`class Config`, `@validator`, `@root_validator`).

### SQLAlchemy 2.0 async only
Use `select()`, `await session.execute(...)`, `await session.scalar(...)`. Never use `session.query()` — it is the legacy ORM v1 API and is incompatible with the async engine.

### RLS trust
Never filter by `tenant_id` manually in application queries. PostgreSQL RLS enforces isolation automatically once `set_config('app.tenant_id', ...)` is called per session. Use `get_tenant_db()` for all runtime queries.

### Type annotations
All function signatures must be annotated. Use `from __future__ import annotations` at the top of every file. Use `X | Y` union syntax (Python 3.10+), not `Optional[X]` or `Union[X, Y]`.

### Commit format
`<type>(<scope>): <description>` — Conventional Commits spec. Subject ≤ 72 chars, imperative mood, no capital after colon. See `CLAUDE.md` for full type/scope tables.

## Entry points

| Command | What it does |
|---------|-------------|
| `uvicorn humetric.api:app --reload --port 8002` | Start API server (dev) |
| `python -m humetric.seed --tenant <id> --ad "<name>" --api-key <key>` | Seed tenant + API key |
| `python worker.py` | Start background signal processor |
| `alembic upgrade head` | Apply all pending migrations |
| `alembic revision --autogenerate -m "<desc>"` | Generate a new migration |
| `pytest tests/ -x -q` | Run tests (requires Docker PostgreSQL on port 5434) |

## Adding a feature — checklist

When adding a new feature, follow this order:

1. Add Pydantic models to `schema.py`.
2. Add route handler to `api.py` (import auth dependency from `middleware/auth.py`).
3. Add data-access methods to `store.py`.
4. Create Alembic migration if new DB columns or tables are needed.
5. Add tests in `tests/`.
6. Update `CLAUDE.md` if architecture decisions change.

## Files agents must not modify

- `alembic/env.py` — Alembic environment config (change only if migration setup changes)
- `.env` — local secrets (never commit)
- `deploy/terraform/terraform.tfvars.example` — update only when adding new variables to `variables.tf`

## Known constraints

- **Tests require Docker.** All tests fail with connection errors if PostgreSQL+pgvector is not running. This is expected in environments without Docker. The `tests/` directory is gitignored — tests exist locally but are not published.
- **`EMBED_DIM` is fixed at runtime.** Changing the embedding provider may require a migration to resize the pgvector column. Do not change `EMBED_DIM` without a migration.
- **Anthropic model pinning.** Agent model names are read from `config.py`. Do not hardcode model strings in agent files.
- **BYO keys are encrypted.** Tenant-supplied Anthropic/Voyage API keys are stored AES-256-GCM encrypted. Access them only through the `agents/base.py` key-resolution path, never directly from the `Tenant` ORM model.
