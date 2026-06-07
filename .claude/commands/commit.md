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

## Step 3 — Python syntax check

For each `.py` file that appears as added or modified in the diff, run:

```bash
python -m py_compile src/humetric/some_module.py
```

Run each file individually. Collect all errors. If any produce stderr output (syntax error), report them and **stop** — do not proceed to commit until fixed.

Skip this step if no `.py` files are in the diff.

---

## Step 4 — Test suite (best-effort)

```bash
pytest tests/ -x -q --tb=short --timeout=30
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
| `mcp` | `mcp_server.py` |

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
- No pytest installed: skip Step 4 with a note, continue.
- Database unavailable (connection errors in pytest output): skip tests, note it, continue.
- Empty diff: abort in Step 2.
