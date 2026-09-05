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
| LLM | Multi-provider via BYOK (Anthropic, OpenAI, Google, DeepSeek). Per-agent models resolve at runtime from `config.py` — never hardcode a model name. |
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
    curator.py     — finalize_merge(extracted, existing, pack) — deterministic, no LLM
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

**The full rulebook is [`.claude/rules/STANDARDS.md`](.claude/rules/STANDARDS.md)** — categorised,
numbered, and greppable, with the exemption list that says what *not* to flag. The essentials are
repeated below; the rulebook wins on any disagreement.

### Async-first
All database calls, agent calls, and I/O are `async`. Never introduce synchronous blocking calls (`requests.get`, synchronous SQLAlchemy sessions) into async code paths.

### Pydantic v2 only
Use `model_config = ConfigDict(...)`, `@model_validator(mode="after")`, `@field_validator`. Do not use v1 patterns (`class Config`, `@validator`, `@root_validator`).

### SQLAlchemy 2.0 async only
Use `select()`, `await session.execute(...)`, `await session.scalar(...)`. Never use `session.query()` — it is the legacy ORM v1 API and is incompatible with the async engine.

### RLS trust
Use the tenant-scoped session for every runtime query — `Depends(_get_tenant_session)` in `api.py`, `get_tenant_db()` elsewhere. PostgreSQL RLS enforces isolation once `set_config('app.tenant_id', ...)` runs for the session, and fails closed when it does not. An explicit `.where(X.tenant_id == tenant_id)` on top of RLS is intentional defence-in-depth — keep it. Every new tenant-scoped table needs its RLS policy in the same migration.

### Type annotations
All function signatures must be annotated. Use `from __future__ import annotations` at the top of every file. Use `X | Y` union syntax (Python 3.10+), not `Optional[X]` or `Union[X, Y]`.

### Commit format
`<type>(<scope>): <description>` — Conventional Commits spec. Subject ≤ 72 chars, imperative mood, no capital after colon. See `CLAUDE.md` for full type/scope tables.

## Entry points

| Command | What it does |
|---------|-------------|
| `uvicorn humetric.api:app --reload --port 8002` | Start API server (dev) |
| `python -m humetric.seed --tenant <id> --name "<name>" --api-key <key>` | Seed tenant + API key |
| `python worker.py` | Start background signal processor |
| `alembic upgrade head` | Apply all pending migrations |
| `alembic revision --autogenerate -m "<desc>"` | Generate a new migration |
| `pytest tests/ -x -q` | Run tests (needs a live PostgreSQL+pgvector — port per [`LOCAL_RUN.md`](LOCAL_RUN.md#portlar): 5433 Homebrew, 5434 Docker) |

## Adding a feature — checklist

When adding a new feature, follow this order:

1. Add Pydantic models to `schema.py`.
2. Add route handler to `api.py` (import auth dependency from `middleware/auth.py`).
3. Add data-access methods to `store.py`.
4. Create Alembic migration if new DB columns or tables are needed.
5. Add tests in `tests/`.
6. Update `CLAUDE.md` if architecture decisions change, and `.claude/rules/STANDARDS.md` if a new recurring risk needs a rule.

## Files agents must not modify

- `alembic/env.py` — Alembic environment config (change only if migration setup changes)
- `.env` — local secrets (never commit)
- `deploy/terraform/terraform.tfvars.example` — update only when adding new variables to `variables.tf`
- `.claude/settings.local.json` — local, untracked permission state

## Known constraints

- **Tests require Docker.** All tests fail with connection errors if PostgreSQL+pgvector is not running. This is expected in environments without Docker. The `tests/` directory is gitignored — tests exist locally but are not published.
- **`EMBED_DIM` is fixed at runtime.** Changing the embedding provider may require a migration to resize the pgvector column. Do not change `EMBED_DIM` without a migration.
- **Model names are configuration, not code.** Every agent resolves its model at runtime through `config.get_*_model()`. A model string anywhere outside `config.py` is a blocking review finding (`HM-ARCH-01`) — this includes docs, prompts, and comments.
- **BYO keys are encrypted.** Tenant-supplied provider keys are stored AES-256-GCM encrypted. Access them only through the key-resolution path, never directly from the `Tenant` ORM model.

## Before you commit

Run `/pre-commit` in Claude Code — quality gates, migration safety, and a review against the
rulebook — then `/commit`. Outside Claude Code, run the gates by hand:
`.venv/bin/ruff check src/` and `.venv/bin/pytest -x -q --timeout=30`.
