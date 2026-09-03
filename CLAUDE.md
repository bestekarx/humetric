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
> **separate project**, checked out alongside this one as `../humetric-site`
> — a standalone Node/Express + React/Vite app that talks to this API over HTTP (`/v1/*`). Do
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

> 🚀 **"Run it" / "run it locally" → follow [`LOCAL_RUN.md`](LOCAL_RUN.md).**
> That file is the canonical recipe for bringing up the **full stack** (Postgres
> + API + worker + the `../humetric-site` backend/frontend) **without Docker**,
> using a local Homebrew PostgreSQL 16 + pgvector on port **5433**. Use the
> Docker steps below only when Docker Desktop is actually running.
>
> 🔎 **"Get X from local" / any question about local data → use
> [`LOCAL_DB.md`](LOCAL_DB.md)** and query directly. It documents both local
> stores: the humetric Postgres (`localhost:5433/humetric`, `psql`) and the site's
> SQLite (`../humetric-site/data/humetric.db`, `sqlite3`), plus the RLS caveat.
> Reads are free; ask before any write.

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
python -m humetric.seed --tenant default --name "Default Tenant" --api-key admin

# 6. Start the API server
uvicorn humetric.api:app --reload --port 8002
```

API: `http://localhost:8002`  
Swagger: `http://localhost:8002/docs`

## Running tests

Tests require a live PostgreSQL+pgvector instance. The Docker Compose stack provides one on port **5434**; the Docker-free Homebrew setup in `LOCAL_RUN.md` uses **5433**. Both are valid — check which one your `.env` points at.

```bash
pytest                        # all tests
pytest -x -q --tb=short      # fail-fast, quiet
pytest tests/test_api.py -v   # single file, verbose
```

**Note:** `tests/` is gitignored in this repository. Contributors should write tests locally; they are not pushed to the public repo. CI runs against a pgvector container defined in `.github/workflows/ci.yml`.

## Quality gates

Run these before committing — they are exactly what `.github/workflows/ci.yml` runs:

```bash
.venv/bin/ruff check src/                          # ruff is NOT on PATH — use the venv path
python -m py_compile <changed .py files>
.venv/bin/pytest -x -q --tb=short --timeout=30     # needs a live database
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
```

`/pre-commit` runs these for you and reviews the diff on top of them.

## Coding standards

**The rulebook is [`.claude/rules/STANDARDS.md`](.claude/rules/STANDARDS.md)** — rule bodies live
there and are deliberately not duplicated here. It covers security and tenant isolation (`HM-SEC-*`,
`HM-DATA-*`), architectural boundaries (`HM-ARCH-*`), conventions (`HM-CONV-*`), process
(`HM-PROC-*`), the reasoning checklist (`HM-REAS-*`), plus common pitfalls, the exemption list, and
the technical debt that must **not** be flagged on unrelated commits.

The short version: Python 3.11+, `from __future__ import annotations`, async everywhere, Pydantic
v2 and SQLAlchemy 2.0 idioms only, `X | Y` unions, `logging` not `print`, and never a hardcoded
model name outside `config.py`.

## Agent architecture

| Agent | Signature |
|-------|-----------|
| `extractor.extract_metrics()` | `(signal_text, pack, tenant_id) → list[ExtractedMetric]` |
| `curator.curate_metrics()` | `(extracted, history, pack, tenant_id) → list[CuratedMetric]` |
| `ranker.rerank()` | `(query, candidates, tenant_id) → list[RankedResult]` |
| `wizard.generate_pack()` | `(description, tenant_id) → MetricPack YAML` |

HuMetric is multi-provider (Anthropic, OpenAI, Google, DeepSeek) via BYOK — never hardcode a specific model name in docs, prompts, or code comments. The active provider and its per-agent model are resolved at runtime (`config.get_extractor_model()` and friends) from the tenant's configured provider, defaulting to `anthropic` when unset. The three LLM-backed agents — `extractor`, `ranker` and `wizard` — call `agents/multi_llm.py:structured_call_multi()`, which dispatches to the resolved provider's SDK, handles retries (`LLM_MAX_RETRIES`), and records token usage via `usage_service`. `curator` is deliberately *not* one of them: merging extracted metrics with history is pure Python (`agents/curator.py` imports no LLM client), so the merge stays deterministic and free.

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
- Use the tenant-scoped session for all runtime queries (`Depends(_get_tenant_session)` in `api.py`,
  `get_tenant_db()` elsewhere). 37 of 44 routes do.
- Use `get_db()` only for health checks, admin, and migration operations — today just
  `/healthz/db` and `/healthz/worker`.
- An explicit `.where(X.tenant_id == tenant_id)` **alongside** RLS is intentional
  defence-in-depth and is used in ~12 places. RLS is the fail-closed backstop, not a reason to
  drop the filter.
- Every new tenant-scoped table needs its RLS policy and `humetric_app` grant **in the same
  migration** — see `HM-DATA-02`. This was missed once already (`006` → retrofitted by `015`).

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

## Slash commands

Two commands, in order. Both live in `.claude/commands/`.

### `/pre-commit` — analyse, do not commit

Pre-commit review. Pick which modules to run:

| Module | What it does |
|---|---|
| Otomatik Kapılar | `py_compile`, `ruff check src/`, optionally pytest and the Alembic round-trip |
| Migration Güvenliği | Alembic risk matrix — data loss, locks, and the missing-RLS-policy check |
| Kod Review | The diff against `.claude/rules/STANDARDS.md`, including the `HM-REAS-*` reasoning pass |
| Commit Mesajı | Drafts an English Conventional Commits message from the real diff |

It reports in Turkish, ends with a `COMMIT EDİLEBİLİR / DÜZELTME GEREKLİ / BLOKLANMALI` verdict,
and **never commits**. When the diff invalidates something in this file or in `STANDARDS.md`, it
offers to update it — never without approval.

### `/commit` — verify and commit

1. Collects the diff.
2. `python -m py_compile` on each changed `.py` file — aborts on syntax errors.
3. `pytest tests/ -x -q --tb=short --timeout=30` — skips gracefully if the database is unavailable.
4. Generates a Conventional Commits message.
5. Presents it for approval, then commits.

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

Because `tests/` is gitignored, a missing test never blocks a commit — `/pre-commit` reports it as
a non-blocking note (`HM-PROC-01`).

## Adding a new API endpoint

1. Add Pydantic request/response models to `schema.py`.
2. Add the route handler to `api.py` (use auth dependency from `middleware/auth.py`).
3. Add data-access methods to `store.py`.
4. Create an Alembic migration if new DB columns are needed.
5. Write tests in `tests/`.

## Common pitfalls

See [`.claude/rules/STANDARDS.md` §9](.claude/rules/STANDARDS.md) — missing `await`,
`session.query()`, direct embedding writes, `config.require_keys()` in tests, and the 5433/5434
database mix-up.

## Architecture decisions

- **Single-file API (`api.py`):** all routes in one file for discoverability. The file has since
  grown past the ~1500-line threshold at which it was meant to be split into router modules under
  `src/humetric/routers/`. That split is accepted technical debt (`STANDARDS.md` §10.1) — it is not
  a finding on unrelated commits, but new route groups should not make it worse.
- **PostgreSQL task queue:** eliminates a separate broker dependency. Uses `SELECT FOR UPDATE SKIP LOCKED` for safe concurrent consumption. The worker is a simple `asyncio` loop in `worker.py`.
- **Externalized prompts:** prompts live in `prompts/*.md` so they can be reviewed, versioned, and overridden per pack without touching Python code.
- **Multi-provider embeddings:** provider selected at startup via `HUMETRIC_EMBEDDING_PROVIDER`. All providers normalise to a fixed vector dimension; changing `EMBED_DIM` requires a schema migration.
- **Fail-closed RLS:** missing tenant context returns zero rows, never a data leak.
