---
description: Scan git diff, verify app health, generate a Conventional Commit message, and commit with user approval
---

# Smart Commit

Analyse all staged and unstaged changes, verify the application is in a valid state, propose a Conventional Commits message, and — after explicit user approval — perform the commit.

## Arguments

```text
$ARGUMENTS
```

Use any hint in `$ARGUMENTS` (e.g., partial type or scope) to guide message generation. Always complete all verification steps regardless.

---

## Step 1 — Verify Git repository

```bash
git rev-parse --is-inside-work-tree
```

If this fails, stop: "Not inside a Git repository."

---

## Step 2 — Collect diff

```bash
git diff HEAD
```

If empty, try:

```bash
git diff --cached
```

If both are empty, check `git status --short` for untracked files. If there is genuinely nothing to commit, report it and stop.

Parse the diff to identify:
- All changed file paths
- Changed `.py` files (for syntax checking in Step 3)
- Dominant module or layer (for scope selection in Step 5)
- Nature of change: new files, modifications, deletions

---

## Step 3 — Quality gates

> The gate list is **not** defined here. The single source of truth is
> [`.claude/rules/STANDARDS.md` §7](../rules/STANDARDS.md), which mirrors
> `.github/workflows/ci.yml`. Run what that section lists; the commands below are the
> current contents of it, repeated only so this command is runnable in one pass.
> If the two ever disagree, STANDARDS.md wins and this file needs updating.

```bash
.venv/bin/ruff check src/                     # ruff is NOT on PATH — use the venv path
python -m py_compile <each changed .py file>
```

Run `py_compile` per file and collect all errors. Any syntax error or ruff finding **on a
file the diff touches**: report and **stop**. Pre-existing findings in untouched files are
listed in STANDARDS.md §10 and are not this commit's problem.

Skip both if no `.py` files are in the diff.

If the diff touches `alembic/versions/`, also run the round-trip:

```bash
.venv/bin/alembic upgrade head && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head
```

A failing downgrade fails CI (`HM-DATA-04`). Skip with a note if the database is unavailable.

**This command does not review the diff against the rulebook.** For the HM-* review,
migration risk matrix, and reasoning pass, run `/pre-commit` first — it analyses and never
commits.

---

## Step 4 — Test suite (best-effort)

```bash
.venv/bin/pytest tests/ -x -q --tb=short --timeout=30
```

Interpret exit codes:

- **0**: tests pass — continue.
- **1 with actual test failures**: display the failure summary. Ask the user whether to proceed. If no, stop; if yes, continue with a note that tests were bypassed at user request.
- **Connection errors** in output (`could not connect to server`, `connection refused`, `asyncpg`, `psycopg`): database unavailable — record `tests: SKIPPED (no DB)` and continue. This is normal in local dev without Docker.
- **ModuleNotFoundError** or any import error: record `tests: SKIPPED (import error)` and continue.

---

## Step 5 — Generate commit message

Analyse the diff and generate a Conventional Commits message.

### Type selection

| Type | When to use |
|------|-------------|
| `feat` | New feature, endpoint, or capability |
| `fix` | Bug fix or incorrect behaviour corrected |
| `docs` | Documentation, docstrings, README only |
| `style` | Formatting, import sorting, whitespace — no logic change |
| `refactor` | Restructured code, no behaviour change |
| `perf` | Performance improvement |
| `test` | Test files only |
| `chore` | Config, tooling, dependencies, non-code maintenance |
| `ci` | GitHub Actions, CI/CD workflows |
| `build` | Dockerfile, pyproject.toml, docker-compose, Alembic |

### Scope selection

| Scope | Files |
|-------|-------|
| `api` | `src/humetric/api.py` |
| `store` | `src/humetric/store.py` |
| `worker` | `src/humetric/worker.py` |
| `agents` | `src/humetric/agents/` |
| `schema` | `src/humetric/schema.py` |
| `config` | `src/humetric/config.py` |
| `auth` | `src/humetric/auth.py` |
| `kvkk` | `src/humetric/kvkk.py` |
| `embeddings` | `src/humetric/embeddings.py` |
| `rag` | `src/humetric/rag.py` |
| `decay` | `src/humetric/decay.py` |
| `middleware` | `src/humetric/middleware/` |
| `migrations` | `alembic/versions/` |
| `packs` | `packs/` |
| `prompts` | `prompts/` |
| `ci` | `.github/workflows/` |
| `docs` | `docs/` |
| `mcp` | `src/humetric/mcp_server.py` |

Omit scope when changes span more than 3 unrelated scopes.

### Subject line rules
- Imperative mood, present tense (`add`, not `added`)
- No capital letter after the colon
- No trailing period
- Maximum **72 characters** total for the first line (type + scope + colon + space + description)

### Body
Include when the motivation is non-obvious. Wrap at 72 chars. Separate from subject with one blank line.

### Footer
- `BREAKING CHANGE: <description>` when public API contracts change
- `Closes #N` when applicable

Present the draft message to the user before committing.

---

## Step 6 — User approval

Ask the user to approve, edit, or reject:

```
Proposed commit message:

feat(store): add tenant key rotation endpoint

Expose PUT /v1/tenant/rotate-api-key so tenants can invalidate
their current key without contacting support.

Accept? (yes / edit / no)
```

- **yes**: use the message as-is.
- **edit**: accept the user's revised message. Validate format and warn (but do not block) if it deviates from Conventional Commits.
- **no**: abort with "Commit aborted."

---

## Step 7 — Stage and commit

```bash
git add -A
```

Then commit with the full message, preserving newlines via a heredoc:

```bash
git commit -m "$(cat <<'EOF'
feat(store): add tenant key rotation endpoint

Expose PUT /v1/tenant/rotate-api-key so tenants can invalidate
their current key without contacting support.
EOF
)"
```

Replace the heredoc body with the approved message text. If `git commit` exits non-zero (pre-commit hook rejection or other error), report the output verbatim and stop without retrying.

---

## Step 8 — Confirm

```bash
git log --oneline -1
```

Report: "Committed: `<sha> <subject>`"

---

## Graceful degradation

- No git: abort in Step 1.
- No Python interpreter: skip Steps 3–4 with a note, continue to Step 5.
- No `.venv/` (ruff/pytest/alembic missing): skip those gates with a note, continue. Do **not**
  fall back to a bare `ruff` / `pytest` on PATH — a different version there produces findings
  CI will not reproduce.
- No pytest installed: skip Step 4 with a note, continue.
- Database unavailable (connection errors in pytest output): skip tests, note it, continue.
- Empty diff: abort in Step 2.
