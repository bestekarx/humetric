# Contributing to HuMetric

Thanks for your interest in contributing! HuMetric is a domain-agnostic
entity intelligence platform that turns unstructured text signals into
calibrated, temporally-decaying entity metrics. This guide explains how to
set up your environment, the conventions we follow, and how to get your
changes merged.

New here? Look for issues labelled
[`good first issue`](https://github.com/bestekarx/humetric/labels/good%20first%20issue)
or [`help wanted`](https://github.com/bestekarx/humetric/labels/help%20wanted).

## Code of Conduct

This project and everyone participating in it is governed by our
[Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to
uphold it. Please report unacceptable behaviour to bestekarx@gmail.com.

## Development setup

HuMetric requires **Python 3.11+** and a PostgreSQL instance with the
`pgvector` extension (provided by the Docker Compose stack).

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY, VOYAGE_API_KEY, HUMETRIC_AUTH_SECRET

# 2. Start PostgreSQL with pgvector
docker compose up -d

# 3. Install the package in editable mode (with dev extras)
pip install -e ".[dev]"

# 4. Run migrations
alembic upgrade head

# 5. Seed a default tenant
python -m humetric.seed --tenant default --ad "Default Tenant" --api-key admin

# 6. Start the API server
uvicorn humetric.api:app --reload --port 8002
```

API: `http://localhost:8002` · Swagger: `http://localhost:8002/docs`

See the [README](README.md) and [docs/](docs/) for a deeper architectural
overview.

## Coding conventions

A short summary — the authoritative, detailed list lives in
[`CLAUDE.md`](CLAUDE.md):

- **Python 3.11+** with `from __future__ import annotations` at the top of
  every module.
- **Async by default** — all DB access, agent calls, and HTTP handlers are
  `async`. Never mix synchronous blocking calls into async paths.
- **Pydantic v2** (`ConfigDict`, `@model_validator(mode="after")`,
  `@field_validator`). No v1 patterns.
- **SQLAlchemy 2.0** — use `select()` / `await session.execute(...)`. The
  legacy `session.query()` API is incompatible with the async engine.
- **Type annotations** on all signatures; use `X | Y` union syntax.
- **Imports:** standard library → third-party → local, one blank line
  between groups. Absolute imports only within `humetric`.
- **Logging:** `_log = logging.getLogger(__name__)` per module; never
  `print()` in production code.
- **Configuration** through `src/humetric/config.py` reading from
  environment variables. Never hardcode secrets, hostnames, or model names.

We lint with [ruff](https://docs.astral.sh/ruff/). Before opening a PR:

```bash
ruff check src/
```

## Running tests

Tests require a live PostgreSQL + pgvector instance (the Docker Compose
stack provides one).

```bash
pytest                       # all tests
pytest -x -q --tb=short      # fail-fast, quiet
pytest tests/test_api.py -v  # single file, verbose
```

> **Note:** `tests/` is gitignored in this repository. Write tests locally —
> they are not pushed to the public repo, but CI runs against a pgvector
> container defined in `.github/workflows/ci.yml`.

When you change behaviour, please add coverage:

- **New endpoints:** at least one happy-path and one error-path test.
- **New agent logic:** unit test mocking the Anthropic client.
- **New migrations:** verify both `alembic upgrade head` and
  `alembic downgrade -1` succeed.
- **New Metric Pack fields:** test in `test_pack_validation.py`.

## Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>
```

- **Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
  `chore`, `ci`, `build`
- **Scopes:** `api`, `store`, `worker`, `agents`, `schema`, `config`,
  `auth`, `kvkk`, `embeddings`, `rag`, `decay`, `middleware`, `migrations`,
  `packs`, `prompts`, `ci`, `docs`, `mcp`
- Subject line ≤ 72 characters, imperative mood, no capital after the colon,
  no trailing period.
- The body explains *why*, not what (wrap at 72 chars).
- Add a `BREAKING CHANGE:` footer when public API contracts change.

## Pull request process

1. Fork the repo and create a branch from `main`. Suggested naming:
   `feat/short-description`, `fix/short-description`, `docs/short-description`.
2. Make one logical change per PR.
3. Ensure `ruff check src/` passes and your tests are green locally.
4. Give the PR a Conventional-Commits-style title (it becomes the squash
   commit message).
5. Fill out the PR template, linking any related issue
   (e.g. `Closes #123`).
6. All CI checks must pass before a maintainer can merge.
7. Breaking changes require a `BREAKING CHANGE:` section and a major version
   bump in `pyproject.toml`.

Never commit `.env`, secrets, or generated build artefacts.

## Reporting bugs and requesting features

- **Bugs / features:** open an issue using the appropriate template.
- **Questions / ideas:** use
  [GitHub Discussions](https://github.com/bestekarx/humetric/discussions).
- **Security vulnerabilities:** do **not** open a public issue — follow
  [SECURITY.md](SECURITY.md).

Thanks for contributing! 🎉
