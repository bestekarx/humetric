# HuMetric — Engine Standards (HM-*)

Rulebook for the `/pre-commit` review. Single source of truth: rule bodies live here and
are **not** repeated in `CLAUDE.md`, `AGENTS.md`, or `CONTRIBUTING.md` — those link here.

## 0. How to read this

**0.1 Categories and default severity**

| Category | Covers | Default severity |
|---|---|---|
| `SEC`  | secrets, authentication, exposure | KRİTİK |
| `DATA` | tenant isolation, migrations, consent, data loss | KRİTİK |
| `ARCH` | architectural boundaries, provider abstraction | ÖNEMLİ |
| `CONV` | language, typing, logging conventions | ÖNEMLİ |
| `PROC` | process and documentation freshness | NOT (never blocks) |
| `REAS` | reasoning prompts — no fixed pattern, judgement required | — |

**0.2 Severity → decision**

- **KRİTİK** > 0 → BLOKLANMALI
- KRİTİK = 0, **ÖNEMLİ** > 0 → DÜZELTME GEREKLİ
- Only **ÖNERİ** / **NOT** → COMMIT EDİLEBİLİR

**0.3 Confidence threshold.** Do not flag what you are not sure of. Read `## 10. Exemptions`
before reporting. Judge **changed lines only** — pre-existing code is not this commit's problem.

**0.4 A rule that cannot be measured is not a rule.** Every rule below carries a `Detect:`
line. If a `Detect:` fires on clean code, the rule is wrong — narrow it or add an exemption.

---

## 1. SEC — Security

### HM-SEC-01 — No secrets, hosts, or IPs in tracked files
**Severity:** KRİTİK · **Scope:** every tracked file
**Rule:** API keys, tokens, passwords, server IPs, hostnames, and personal paths never enter a
tracked file — code, config, docs, or commit message.
**Why:** this repository is public. A pushed secret is compromised even if reverted.
**Detect:** `git diff | grep -nE '(sk-[A-Za-z0-9]|hms_live_|[0-9]{1,3}(\.[0-9]{1,3}){3}|/Users/)'`
**Exception:** documentation examples using `localhost`, `example.com`, or obvious placeholders.
Environment-specific values belong in `.env` (gitignored) or `CLAUDE.local.md`.
**Evidence:** `CLAUDE.md` opening banner.

### HM-SEC-02 — Every route declares a tenant session dependency
**Severity:** KRİTİK · **Scope:** `src/humetric/api.py`
**Rule:** a new `@app.get/post/put/delete` handler takes `db: AsyncSession = Depends(_get_tenant_session)`.
**Why:** `_get_tenant_session` resolves the bearer token to a tenant and sets `app.tenant_id`.
Without it the route either serves no tenant context or runs unauthenticated.
**Detect:** a new route decorator in the diff whose handler signature lacks `_get_tenant_session`.
**Exception:** the seven pre-tenant routes — `/healthz`, `/healthz/db`, `/healthz/worker`
(`api.py:236-252`, public health probes on `get_db`) and `/v1/register`, `/v1/verify-email`,
`/v1/login`, `/v1/billing/webhook` (`api.py:1706`, `:1754`, `:1790`, `:1997`), which run before a
tenant session can exist or are authenticated by a provider signature instead.
**Evidence:** 37 of 44 routes use it; the other 7 are the exceptions listed above.

---

## 2. DATA — Data integrity and tenant isolation

### HM-DATA-01 — `get_db` only for health and admin paths
**Severity:** KRİTİK · **Scope:** `src/humetric/api.py`, `src/humetric/worker.py`
**Rule:** runtime request handling goes through the tenant-scoped session. `get_db` bypasses
RLS setup and is reserved for health checks, migrations, and admin tooling.
**Detect:** `grep -n 'Depends(get_db)' src/humetric/api.py` — today only `api.py:242` and `api.py:252`.
**❌** `async def list_entities(db: AsyncSession = Depends(get_db))`
**✅** `async def list_entities(db: AsyncSession = Depends(_get_tenant_session))`

### HM-DATA-02 — A new tenant-scoped table gets an RLS policy in the same migration
**Severity:** KRİTİK — **priority finding** · **Scope:** `alembic/versions/*.py`
**Rule:** if `op.create_table` adds a table with a `tenant_id` column, the same migration must
enable RLS, create the `tenant_isolation` policy, and grant to the `humetric_app` role.
**Why:** RLS is the fail-closed backstop. A table without a policy is invisible to the
restricted app role (permission denied at runtime) and loses defence-in-depth.
**Detect:** migration contains `create_table` + `tenant_id` but no `POLICY` / `ROW LEVEL SECURITY`.
**Evidence:** **this has already happened.** `006_tenant_stripe_subscription.py` created
`metering_record` with a `tenant_id` and no policy; `015_metering_record_rls.py` had to
retrofit it months later. Its docstring documents the exact failure mode.
**✅ pattern:** take the **grant + `ENABLE ROW LEVEL SECURITY` block** from
`020_llm_call_record.py` and the **comparison expression** from `022_rls_blank_tenant_guc.py`:
`tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::bigint`. The bare
`current_setting(...)::bigint` form aborts the query when the GUC holds an empty string —
which is exactly what `get_tenant_db()` leaves on a pooled connection at teardown.
**Do not copy 020's policy body verbatim:** 022 rewrote every policy at runtime, but 020's
own source still carries the bare form on disk, so it is a broken copy-paste template.

### HM-DATA-03 — Destructive migration operations
**Severity:** KRİTİK / YÜKSEK by operation · **Scope:** `alembic/versions/*.py`

| Severity | Operation | Condition |
|---|---|---|
| KRİTİK | `op.drop_table` / `op.drop_column` | table was **not** created in this same migration |
| KRİTİK | `op.execute("DELETE …")` / `TRUNCATE` | always |
| YÜKSEK | `op.alter_column(nullable=False)` | column was previously nullable — existing NULLs abort the migration |
| YÜKSEK | `op.execute("UPDATE …")` | overwrites existing values irreversibly |
| YÜKSEK | `op.create_index` without `postgresql_concurrently=True` | large table — ACCESS EXCLUSIVE lock blocks all reads and writes |
| DİKKAT | rename table/column | application code must land in the same deploy |
| DİKKAT | NOT NULL column with `server_default` | confirm the backfill value is intended |
| GÜVENLİ | `add_column` (nullable or defaulted), index on a brand-new table, FK with `Restrict`/`SetNull` | do not flag |

**False-positive filter:** a `drop_column` against a table created in the same `upgrade()` is
DİKKAT, not KRİTİK — the table is still empty.
**Safe alternative (drop):** ship two migrations — make the column nullable now, drop it after
the deploy proves nothing reads it.
**Safe alternative (index):** `op.execute("CREATE INDEX CONCURRENTLY …")`, noting that it
cannot run inside a transaction.

### HM-DATA-04 — `downgrade()` reverses exactly what `upgrade()` did
**Severity:** ÖNEMLİ · **Scope:** `alembic/versions/*.py`
**Rule:** every migration is reversible; `downgrade()` drops only what its `upgrade()` created.
**Why:** CI runs `upgrade head → downgrade -1 → upgrade head`. A broken downgrade fails the build.
**Detect:** empty/`pass` body, or a `downgrade()` touching a table its `upgrade()` did not create.
**Evidence:** `018_usage_record_client_dims.py` is the reference shape — index drops then column drops.

### HM-DATA-05 — Sensitive metrics stay behind consent
**Severity:** KRİTİK · **Scope:** `packs/*.yaml`, `src/humetric/kvkk.py`, read paths
**Rule:** a metric that carries personal data is marked `sensitive: true` with a
`requires_consent_scope` in the pack, is excluded from embedding vectors, and disappears from
every read path the moment consent is revoked.
**Detect:** new pack metric keys that look personal without `sensitive:`; a new read path that
returns metrics without routing through the `kvkk` consent filter.

> **Note — explicit `tenant_id` filters are permitted.** `store.py` and `api.py` deliberately
> combine an explicit `.where(X.tenant_id == tenant_id)` with RLS as defence-in-depth (dozens of call
> sites). Do **not** flag this. See `015_metering_record_rls.py`'s docstring.

---

## 3. ARCH — Architectural boundaries

### HM-ARCH-01 — Never hardcode a model name outside `config.py`
**Severity:** KRİTİK · **Scope:** all of `src/`, `prompts/`, `docs/`
**Rule:** HuMetric is multi-provider via BYOK. Provider model identifiers appear **only** as
environment-variable defaults in `config.py`. Everywhere else, resolve at runtime through
`config.get_extractor_model()` and friends. Prose says "the configured provider", never a brand model.
**Why:** a hardcoded model breaks every tenant not using that provider, and silently ignores
their BYOK configuration.
**Detect:** `grep -rnE '"(claude|gpt-[0-9]|gemini-|deepseek-)[a-z0-9.-]*"' src/ --include='*.py' | grep -v config.py` — currently empty.
**Exception:** `src/humetric/config.py:46-62` only.

### HM-ARCH-02 — Agents call `structured_call_multi()`
**Severity:** ÖNEMLİ · **Scope:** `src/humetric/agents/`
**Rule:** a new agent dispatches through `agents/multi_llm.py:structured_call_multi()`, which
resolves the tenant's provider, applies `LLM_MAX_RETRIES`, and records token usage.
**Why:** a direct SDK call skips retry handling and leaves the call unmetered — the tenant is
not billed and usage reports under-report.
**Detect:** `anthropic.`, `openai.`, `genai.` client construction inside `agents/` outside `multi_llm.py`.
**Exception:** `agents/base.py` — the Anthropic provider implementation that
`multi_llm.py:211` delegates to. It constructs the SDK client by design; the rule targets a
*new* agent that bypasses `structured_call_multi()`, not the dispatcher's own backend.

### HM-ARCH-03 — SQLAlchemy 2.0 async only
**Severity:** ÖNEMLİ · **Scope:** all of `src/`
**Rule:** use `select()` + `await session.execute(...)`. The legacy `session.query()` API is
incompatible with the async engine.
**Detect:** `grep -rn 'session\.query(' src/` — currently empty.

### HM-ARCH-04 — No blocking calls on an async path
**Severity:** ÖNEMLİ · **Scope:** all of `src/`
**Rule:** no `time.sleep`, synchronous `requests`, or blocking file I/O inside an `async def`.
Use `asyncio.sleep` and an async HTTP client.
**Detect:** `grep -rnE '\b(time\.sleep|requests\.(get|post|put))\(' src/` — currently empty.

---

## 4. CONV — Conventions

### HM-CONV-01 — Module preamble and typing
**Severity:** ÖNEMLİ · **Scope:** new files in `src/`
**Rule:** `from __future__ import annotations` at the top; type annotations on every signature;
`X | Y` unions, not `Optional[X]` / `Union[X, Y]`; Pydantic v2 idioms only (`model_config`,
`@field_validator`, `@model_validator(mode="after")`).
**Detect:** new `.py` file in the diff without the `__future__` import.
**Exception:** applies to **new** files. Four existing modules predate the rule and are listed in `## 9`.

### HM-CONV-02 — Logging, not printing
**Severity:** ÖNEMLİ · **Scope:** `api.py`, `store.py`, `worker.py`, `agents/`, `middleware/`
**Rule:** `_log = logging.getLogger(__name__)` per module. No `print()` on a runtime path.
**Detect:** `grep -rn '^\s*print(' src/humetric/api.py src/humetric/store.py src/humetric/worker.py src/humetric/agents/ src/humetric/middleware/` — currently empty.
**Exception:** CLI entrypoints (`seed.py`, `generator.py`) print by design.

### HM-CONV-03 — English in tracked code
**Severity:** ÖNEMLİ · **Scope:** `src/`, `alembic/`, commit messages
**Rule:** identifiers, comments, docstrings, and commit messages are English.
**Exception:** `LOCAL_RUN.md`, `LOCAL_DB.md`, `CLAUDE.local.md`, `docs/plans/`, `docs/architecture/`
(repo-internal Mermaid reference, excluded from the VitePress site via `srcExclude`), and prompt text
that is deliberately Turkish.
**Evidence:** commit `673dbe5` translated the last Turkish comments out of the codebase.

### HM-CONV-04 — Specific exception handling
**Severity:** ÖNERİ · **Scope:** all of `src/`
**Rule:** catch specific exception types. A broad `except Exception as exc:` is allowed only on a
deliberate catch-all path, and must log `exc`.
**Detect:** a new bare `except:` or an `except Exception` that swallows without logging.

---

## 5. PROC — Process (never blocks a commit)

### HM-PROC-01 — A new endpoint lands complete
**Severity:** NOT
**Rule:** request/response models in `schema.py` → handler in `api.py` → data access in
`store.py` → Alembic migration if columns changed → a happy-path and an error-path test.
**Report as:** `NOT [HM-PROC-01]: <what is missing>` — informational only.

### HM-PROC-02 — Documentation the diff has invalidated
**Severity:** NOT
**Rule:** if the change makes a sentence in `CLAUDE.md`, `README.md`, or `docs/` untrue — a new
entity, endpoint, env var, or a changed flow — that file should be updated too.
**Ask first:** does this diff falsify a documented sentence? Typos, log-message wording, import
ordering, and one-line guards almost never do. Line count is not the test.

---

## 6. REAS — Reasoning (no fixed pattern; answer these against the diff)

- **HM-REAS-01 — Wrong abstraction.** Does this add a layer nothing else will use, or duplicate
  something that already exists in `store.py` / `rag.py` / `decay.py`?
- **HM-REAS-02 — Boundary case.** Empty list, zero signals, no consent, missing pack, expired
  key, first-ever metric for an entity — what does this code do?
- **HM-REAS-03 — Concurrency.** The worker consumes with `SELECT FOR UPDATE SKIP LOCKED`. Can two
  workers reach this code at once? Is the operation idempotent on replay?
- **HM-REAS-04 — Transaction boundary.** Can this leave a partial write — a signal marked
  processed but its metrics unwritten? Is the whole unit inside one transaction?
- **HM-REAS-05 — Silent behaviour change.** Does this change an existing metric value, decay
  weight, or API response shape without a version bump or a note?

A change that satisfies every mechanical rule above but fails one of these is still a finding.
Report it as `ÖNEMLİ [HM-REAS-0N]`. Skipping this section makes the review incomplete.

---

## 7. Quality gates (CI parity)

```bash
.venv/bin/ruff check src/                          # ruff is NOT on PATH — use the venv path
python -m py_compile <changed .py files>
.venv/bin/pytest -x -q --tb=short --timeout=30     # needs live Postgres+pgvector
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
```

`ruff check src/` is the exact command `.github/workflows/ci.yml` runs.
`pyproject.toml` has no `[tool.ruff]` section, so behaviour follows the installed version's
defaults (currently 0.15.17).

---

## 8. Files agents must not modify

`alembic/env.py` · `.env` · `deploy/terraform/terraform.tfvars.example` ·
`.claude/settings.local.json`

---

## 9. Common pitfalls

Recurring mistakes in this codebase. Not rules with severities — things to check when the diff
touches the area.

- **Forgetting `await`** on an async DB call. SQLAlchemy async raises `MissingGreenlet` at
  runtime, not at import — tests catch it, type checkers do not.
- **Writing an embedding vector directly.** Use `Store.update_entity_embedding()` so the vector
  dimension stays consistent with `EMBED_DIM`.
- **Calling `config.require_keys()` in a test.** The test conftest sets dummy env vars; the real
  key check belongs in production entrypoints only.
- **Hardcoding a tenant id in a test.** Use the `test_tenant` fixture from `tests/conftest.py`.
- **Confusing the two local databases.** Docker Compose exposes PostgreSQL on **5434**; the
  Homebrew instance used by `LOCAL_RUN.md` is on **5433**. Both are valid — check which one the
  `.env` in play points at before concluding something is broken.

---

## 10. Exemptions and technical debt

Known and accepted — **do not flag these on unrelated commits**:

1. **`api.py` is 2365 lines.** `CLAUDE.md` sets a ~1500-line split threshold; the file has passed
   it. Splitting into `src/humetric/routers/` is tracked debt, not this commit's job. Flag only
   if the diff makes the file substantially larger.
2. **`tests/` is gitignored.** Tests are local-only, so "no test added" never blocks a commit.
   Note it under HM-PROC-01 and move on.
3. **Three pre-existing `ruff` F401 findings** (`generator.py:31`, `generator.py:32`,
   `mcp_server.py:42`). Report ruff output only for files the diff touches.
4. **One module lacks `from __future__ import annotations`** — `agents/versioning.py`.
   HM-CONV-01 applies to new files. Note that `batch_worker.py:35`, `generator.py:28` and
   `mcp_server.py:39` *do* have it, below a long module docstring — check the whole header
   before flagging.
5. **Explicit `tenant_id` filters alongside RLS** are intentional defence-in-depth. Never a finding.
