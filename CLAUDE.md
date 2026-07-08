# HuMetric — Claude Code Guide

> ⚠️ **CRITICAL RULE — THIS IS AN OPEN SOURCE PROJECT.** Never commit personal
> information, server addresses/IPs, hostnames, credentials, API keys, tokens,
> or any other secret into any file tracked by git — including code, configs,
> docs, commit messages, and this file. This applies to every new feature,
> script, or MCP integration (e.g. Dokploy) added to the repo. Secrets and
> environment-specific values belong in `.env` (gitignored) or local/user-scope
> tool config (e.g. `claude mcp add --scope local`), never in project-scope
> files like `.mcp.json` or anything committed to the repository.

Domain-agnostic entity intelligence platform. Turns unstructured text signals into calibrated, temporally-decaying entity metrics using a multi-agent LLM pipeline.

> ℹ️ **HuMetric is the open-source backend tool only — it does NOT contain a
> website or dashboard UI.** The customer-facing website and dashboard live in a
> **separate project** at `../humetric-site` (repo:
> `/Users/bestekarx/RiderProjects/humetric-site`), which is a standalone
> Node/Express + React/Vite app that talks to this API over HTTP (`/v1/*`). Do
> not add static-site, landing-page, or dashboard-frontend files to this
> repository. Frontend/site changes belong in the `humetric-site` project. This
> repo only exposes the HTTP API (e.g. `/v1/register`, `/v1/login`,
> `/v1/api-keys`, `/v1/tenant/dashboard`) that the site consumes.

## Quick orientation

```
src/humetric/
  api.py               All FastAPI route handlers (single file)
  store.py             Async data-access layer (SQLAlchemy 2.0)
  worker.py            Background task processor (signal pipeline loop)
  schema.py            All Pydantic v2 request/response models
  config.py            All settings loaded from environment variables
  auth.py              API key hashing + bearer verification
  kvkk.py              GDPR/KVKK consent enforcement
  embeddings.py        Multi-provider embedding abstraction
  rag.py               Hybrid retrieval (vector + full-text)
  decay.py             Temporal decay weighting
  agents/
    base.py            Anthropic client wrapper (structured_call)
    extractor.py       extract_metrics() — signal text → metric list
    curator.py         curate_metrics() — merge with historical data
    ranker.py          rerank() — LLM re-ranking for hybrid search
    wizard.py          generate_pack() — natural language → MetricPack YAML
  db/
    database.py        Async engine, session factories, RLS helpers
    models.py          SQLAlchemy ORM (9 tables)
  middleware/          Auth, rate limit, billing guard, Prometheus
  services/            Stripe, email, captcha, usage metering
alembic/versions/      Database migration scripts
packs/                 Metric Pack YAML definitions
prompts/               Externalized LLM prompts (*.md)
tests/                 pytest suite — gitignored, local dev only
```

## Development setup

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY, VOYAGE_API_KEY, HUMETRIC_AUTH_SECRET

# 2. Start PostgreSQL with pgvector
docker compose up -d

# 3. Install package in editable mode
pip install -e ".[dev]"

# 4. Run migrations
alembic upgrade head

# 5. Seed a default tenant
python -m humetric.seed --tenant default --ad "Default Tenant" --api-key admin

# 6. Start the API server
uvicorn humetric.api:app --reload --port 8002
```

API: `http://localhost:8002`  
Swagger: `http://localhost:8002/docs`

## Running tests

Tests require a live PostgreSQL+pgvector instance. The Docker Compose stack provides one on port 5434.

```bash
pytest                        # all tests
pytest -x -q --tb=short      # fail-fast, quiet
pytest tests/test_api.py -v   # single file, verbose
```

**Note:** `tests/` is gitignored in this repository. Contributors should write tests locally; they are not pushed to the public repo. CI runs against a pgvector container defined in `.github/workflows/ci.yml`.

## Python conventions

- **Python 3.11+** throughout. Use `from __future__ import annotations` at the top of every module.
- **Async by default.** All database access, agent calls, and HTTP handlers are `async`. Never mix synchronous blocking calls into async code paths.
- **Pydantic v2.** Use `model_config = ConfigDict(...)`, `@model_validator(mode="after")`, `@field_validator`. Do not use v1 patterns (`class Config`, `@validator`, `@root_validator`).
- **SQLAlchemy 2.0.** Use `select()`, `await session.execute(...)`, `await session.scalar(...)`. The legacy `session.query()` API is incompatible with the async engine.
- **Type annotations** on all function signatures. Use `X | Y` union syntax (not `Optional[X]` or `Union[X, Y]`).
- **Imports:** standard library → third-party → local (one blank line between groups). Absolute imports only within `humetric`.
- **Logging:** `_log = logging.getLogger(__name__)` per module. Use structured log messages. Never use `print()` in production code.
- **Exception handling:** always catch specific types. Use `except Exception as exc:` only for broad catch-all paths, and always log `exc`.
- **Configuration:** all tunables go through `src/humetric/config.py` reading from environment variables. Never hardcode secrets, hostnames, or model names.

## Agent architecture

| Agent | Model | Signature |
|-------|-------|-----------|
| `extractor.extract_metrics()` | claude-haiku-4-5 | `(signal_text, pack, tenant_id) → list[ExtractedMetric]` |
| `curator.curate_metrics()` | claude-sonnet-4-6 | `(extracted, history, pack, tenant_id) → list[CuratedMetric]` |
| `ranker.rerank()` | claude-sonnet-4-6 | `(query, candidates, tenant_id) → list[RankedResult]` |
| `wizard.generate_pack()` | claude-haiku-4-5 | `(description, tenant_id) → MetricPack YAML` |

All agents call `agents/base.py:structured_call()` which wraps the Anthropic SDK, handles retries (`LLM_MAX_RETRIES`), and records token usage via `usage_service`.

Prompts are externalized in `prompts/*.md` and loaded at import time via `agents/__init__.py`. To override a prompt for a specific pack, set the `prompts.extraction` key in the Pack YAML.

Adding a new agent: create `src/humetric/agents/myagent.py`, use `structured_call()` for the LLM call, add the prompt to `prompts/myagent-default.md`.

## Multi-tenant RLS

Row-Level Security is enforced at the PostgreSQL level. Every request resolves a bearer token to a `tenant_id`, then:

```python
await session.execute(
    text("SELECT set_config('app.tenant_id', :tid, false)"),
    {"tid": str(tenant_id)},
)
```

PostgreSQL policies on each table filter rows automatically. **Fail-closed:** if `app.tenant_id` is not set, zero rows are returned — no data leak is possible.

Rules:
- Use `get_tenant_db()` for all runtime queries (RLS active).
- Use `get_db()` only for admin/migration operations.
- Never pass `tenant_id` as a WHERE clause from application code — trust RLS.

## KVKK / GDPR compliance

Sensitive metrics are flagged in the Pack YAML with `sensitive: true` and `requires_consent_scope`. `kvkk.py` enforces:
- Consent must be explicitly granted before sensitive metrics are returned.
- Sensitive metric keys are excluded from embedding vectors.
- Revoking consent immediately hides the metric from all read paths.

When adding new data fields: check whether they qualify as sensitive and apply the flags.

## Commit conventions

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body — wrap at 72 chars]

[optional footer(s)]
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`, `build`

**Scopes:** `api`, `store`, `worker`, `agents`, `schema`, `config`, `auth`, `kvkk`, `embeddings`, `rag`, `decay`, `middleware`, `migrations`, `packs`, `prompts`, `ci`, `docs`, `mcp`

**Rules:**
- Subject line ≤ 72 characters total.
- Imperative mood, no capital after colon, no trailing period.
- Body explains *why*, not what. Wrap at 72 chars.
- `BREAKING CHANGE:` footer required when public API contracts change.

## /commit slash command

Available in both Claude Code and OpenCode. Type `/commit` to run it.

What it does:
1. Runs `git diff HEAD` to collect all changes.
2. Runs `python -m py_compile` on each changed `.py` file — aborts on syntax errors.
3. Runs `pytest tests/ -x -q --tb=short --timeout=30` — skips gracefully if the database is unavailable.
4. Generates a Conventional Commits message from diff analysis.
5. Presents the message for approval before committing.

Usage: `/commit` or `/commit feat: my hint` to seed the type/description.

## Pull request guidelines

- One logical change per PR.
- PR title must follow Conventional Commits format.
- Description explains *why* the change is needed.
- All CI checks must pass before merge.
- Breaking changes require a `BREAKING CHANGE:` section and a major version bump in `pyproject.toml`.
- Never commit `.env`, secrets, or generated build artefacts.

## Testing requirements

- New endpoints: at least one happy-path and one error-path test.
- New agent logic: unit test mocking the Anthropic client.
- New migrations: verify `alembic upgrade head` + `alembic downgrade -1` both succeed.
- New Metric Pack fields: test in `test_pack_validation.py`.
- All changed modules should have test coverage.

## Adding a new API endpoint

1. Add Pydantic request/response models to `schema.py`.
2. Add the route handler to `api.py` (use auth dependency from `middleware/auth.py`).
3. Add data-access methods to `store.py`.
4. Create an Alembic migration if new DB columns are needed.
5. Write tests in `tests/`.

## Common pitfalls

- **Forgetting `await`** on async DB calls — SQLAlchemy async raises `MissingGreenlet` at runtime.
- **Using `session.query()`** — legacy API, incompatible with async engine. Always use `select()`.
- **Hardcoding tenant IDs in tests** — use the `test_tenant` fixture from `tests/conftest.py`.
- **Writing embedding vectors directly** — use `Store.update_entity_embedding()` to keep the vector dimension consistent with `EMBED_DIM`.
- **Calling `config.require_keys()` in tests** — the test conftest sets dummy env vars; the real key check should only run in production entrypoints.

## Architecture decisions

- **Single-file API (`api.py`):** all routes in one file for discoverability. If it grows beyond ~1500 lines, split into router modules under `src/humetric/routers/`.
- **PostgreSQL task queue:** eliminates a separate broker dependency. Uses `SELECT FOR UPDATE SKIP LOCKED` for safe concurrent consumption. The worker is a simple `asyncio` loop in `worker.py`.
- **Externalized prompts:** prompts live in `prompts/*.md` so they can be reviewed, versioned, and overridden per pack without touching Python code.
- **Multi-provider embeddings:** provider selected at startup via `HUMETRIC_EMBEDDING_PROVIDER`. All providers normalise to a fixed vector dimension; changing `EMBED_DIM` requires a schema migration.
- **Fail-closed RLS:** missing tenant context returns zero rows, never a data leak.
