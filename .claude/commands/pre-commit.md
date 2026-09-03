---
description: Pre-commit analysis — run the quality gates, review the diff against the standards, assess migration safety, and draft a commit message without committing
---

# Pre-Commit Analysis

You are a pre-commit analysis agent. You scan the working tree **before** a commit and report on
code quality, security, and architectural consistency. You end with a clear verdict on whether
the change is safe to commit.

**This command never commits.** It produces a report and a draft message; `/commit` does the
committing.

## Arguments

```text
$ARGUMENTS
```

Treat any hint in `$ARGUMENTS` (a module name, a commit type, "sadece review") as a preference
for which modules to run. Still ask in ADIM 2 unless the hint is unambiguous.

## Execution philosophy

- **Run only the selected modules.** For an unselected module do nothing at all — no diff
  reading, no file reading, no commands.
- **Report in Turkish.** Findings, tables, and the verdict are Turkish. The drafted commit
  message is English (this repository's history is English Conventional Commits).
- **No false positives.** Do not flag what you are not sure of. Read
  `.claude/rules/STANDARDS.md` §10 Exemptions before reporting anything.
- **Judge changed lines only.** Pre-existing problems are not this commit's fault.
- **Read the diff once, reuse it everywhere.** Never run the same command twice.
- **Decide.** The report ends with a yes/no answer to "can this be committed?"

---

## ADIM 1 — Scan and classify the changes

```bash
git status --short
git diff --stat HEAD
```

### 1.1 Nothing to commit

If staged, unstaged, and untracked are all empty, print
`Commit edilecek değişiklik bulunamadı.` and stop.

### 1.2 Classify the changed files

| Category | Pattern |
|---|---|
| Migration | `alembic/versions/*.py` |
| Route | `src/humetric/api.py` |
| Store | `src/humetric/store.py` |
| Worker | `src/humetric/worker.py` |
| Agent | `src/humetric/agents/**` |
| Entity | `src/humetric/db/models.py` |
| Schema | `src/humetric/schema.py` |
| Config | `src/humetric/config.py` |
| Middleware | `src/humetric/middleware/**` |
| Pack | `packs/*.yaml` |
| Prompt | `prompts/*.md` |
| Docs | `README.md`, `CLAUDE.md`, `docs/**`, `*.md` |
| CI/Build | `.github/**`, `Dockerfile*`, `pyproject.toml`, `docker-compose*` |

### 1.3 Size notice

If the total change is **500+ lines**, print — informational, not a blocker:

```
Büyük değişiklik tespit edildi (~X satır). Commit'i mantıksal parçalara bölmeyi düşünün.
```

---

## ADIM 2 — Choose the modules

Ask with `AskUserQuestion`, `multiSelect: true`:

- Question: `Hangi kontrolleri çalıştırayım?`
- Options: `Otomatik Kapılar` · `Migration Güvenliği` · `Kod Review` · `Commit Mesajı`

`Hafıza & Standart Besleme` is not an option — it runs automatically in ADIM 6 whenever Kod
Review produced a candidate, and writes nothing without approval.

### 2.1 Smart suggestions

Prepend to the question text, based on ADIM 1.2:

- Migration file present → `(Migration dosyası var — Migration Güvenliği önerilir)`
- `api.py` changed → `(Route değişikliği var — Kod Review önerilir)`
- `config.py` or `agents/**` changed → `(Sağlayıcı/model yapılandırması değişti — Kod Review önerilir)`
- Only `*.md` changed → `(Yalnızca doküman değişti — Otomatik Kapılar atlanabilir)`

---

## ADIM 3 — Otomatik Kapılar

> Runs only if selected.

These mirror `.github/workflows/ci.yml` exactly. Run the cheap ones unconditionally; ask before
the slow ones.

### 3.1 Syntax

For each added or modified `.py` file:

```bash
python -m py_compile <file>
```

Any stderr is a **KRİTİK** finding. Skip this step if no `.py` file changed.

### 3.2 Lint

```bash
.venv/bin/ruff check src/
```

`ruff` is **not on PATH** — always use the venv path. Runs in well under a second.

Report only findings in files the diff touches. Three findings are pre-existing and exempt
(`STANDARDS.md` §10.3): `generator.py:31`, `generator.py:32`, `mcp_server.py:42`. A ruff finding
in a changed file is **KRİTİK** — CI's lint job fails on it.

### 3.3 Tests (ask first)

The suite needs a live PostgreSQL+pgvector, so ask before running it:

```bash
.venv/bin/pytest -x -q --tb=short --timeout=30
```

- exit 0 → pass.
- failures → show the summary, ask whether to continue.
- `could not connect`, `connection refused`, `asyncpg`, `psycopg` in the output → record
  `testler: ATLANDI (DB yok)` and continue. This is normal in local dev.
- `ModuleNotFoundError` / import error → `testler: ATLANDI (import hatası)`, continue.

### 3.4 Migration round-trip (only if a migration changed, ask first)

```bash
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
```

Needs a live database. Skip with a note if unavailable.

### 3.5 Gate report

```
Otomatik Kapılar
─────────────────────────────────
py_compile : TEMİZ / X hata
ruff       : TEMİZ / X bulgu (değişen dosyalarda)
pytest     : GEÇTİ / ATLANDI (sebep) / X başarısız
alembic    : GEÇTİ / ATLANDI (sebep) / uygulanmadı
```

**Graceful degradation:** `.venv/bin/ruff` missing → skip with a note. No `.venv` → skip 3.2–3.4.
A skipped gate is a note, never a blocker. A **failed** gate is a KRİTİK finding.

---

## ADIM 4 — Migration Güvenliği

> Runs only if selected. If ADIM 1.2 found no `alembic/versions/*.py`, print
> `Migration dosyası bulunamadı, kontrol atlanıyor.` and skip.

### 4.1 Read the migration

Read each migration file in full — **both `upgrade()` and `downgrade()`**.

### 4.2 Context first (false-positive prevention)

Before assigning severity, establish:

1. **Which tables this migration creates itself.** Destructive operations on them are safe — the
   table is still empty.
2. **Whether the affected table already exists** in an earlier migration.
3. **The direction of a foreign key.** Cascade from a child/detail/log table to its parent is
   acceptable; cascade onto a master/lookup table is not.
4. **What the column actually holds.** Dropping a boolean flag is not dropping a text body.

### 4.3 Risk matrix

Apply `STANDARDS.md` **HM-DATA-02** (RLS policy on a new tenant table — a priority finding) and
**HM-DATA-03** (the destructive-operation table), plus **HM-DATA-04** for `downgrade()`.

The single highest-value check: a migration that runs `op.create_table` with a `tenant_id`
column but never creates the RLS policy and the `humetric_app` grant. This has already shipped
once — `006_tenant_stripe_subscription.py` created `metering_record` without a policy and
`015_metering_record_rls.py` had to retrofit it. Copy the policy block from
`020_llm_call_record.py`.

### 4.4 Produce a concrete safe alternative

For every finding, write the replacement using the **real table, column, and schema names** from
the file — not a template. Two-migration nullable-then-drop for a column drop; a backup SQL
script before a table drop; `CREATE INDEX CONCURRENTLY` via `op.execute` for a large-table index
(noting it cannot run inside a transaction); a NULL-backfill `UPDATE` before a
`nullable=False` alter.

### 4.5 Report

```
Migration Güvenlik Raporu
─────────────────────────────────
Migration: {revision} — {dosya}

upgrade() analizi:
  KRİTİK  {operasyon} — {şema}."{tablo}"."{kolon}"
          {tek cümle: ne kaybolur}
          Öneri: {somut alternatif}
  GÜVENLİ {flag üretmeyen operasyonlar}

downgrade() analizi:
  {Geri alma güvenli — yalnızca upgrade()'in yarattığını kaldırıyor. / bulgular}
```

If nothing is found: `Migration güvenli, veri kaybı riski tespit edilmedi.`

### 4.6 Action

If anything was found, ask with `AskUserQuestion`:
`Güvenli hale getir` · `Yedek SQL scripti üret` · `Sadece raporu gör` · `Atla`

- **Güvenli hale getir** — edit the migration. For each edit show: old code (with line number) →
  new code → one line of rationale.
- **Yedek SQL scripti üret** — write `alembic/versions/{revision}_safety.sql`: a
  `CREATE TABLE … AS SELECT *` backup of each affected table, a row-count verification block that
  raises on mismatch, and a commented-out restore section.

---

## ADIM 5 — Kod Review

> Runs only if selected.

### 5.1 Read the diff

```bash
git diff HEAD
```

Read the full current content of each changed file for context. For untracked files, read the
file itself. Do not re-run a command ADIM 3 or 4 already ran.

### 5.2 Apply the standards

**The rules live in `.claude/rules/STANDARDS.md`. Read that file — it is not repeated here.**
Apply it in this order:

1. **§0.3 confidence threshold and §10 exemptions first.** These prevent most false positives.
2. **KRİTİK — blocks the commit:** `SEC` and `DATA`. Missing `_get_tenant_session` on a new
   route, `get_db` on a runtime path, a new tenant table without an RLS policy, a destructive
   migration operation, a hardcoded model name outside `config.py`, a secret in a tracked file,
   sensitive metrics escaping consent enforcement.
3. **ÖNEMLİ:** `ARCH` and `CONV`. Direct SDK calls in `agents/`, `session.query()`, blocking
   calls on an async path, missing `__future__` import in a new file, `print()` on a runtime path.
4. **ÖNERİ:** performance and readability — unnecessary eager loading, N+1 queries, a broad
   `except` that swallows.
5. **`REAS` — do not skip.** These have no grep. Answer HM-REAS-01…05 against this specific
   diff: wrong abstraction, boundary case, worker concurrency, transaction boundary, silent
   behaviour change. **A change that satisfies every mechanical rule but is logically wrong or
   risky must be reported as a `REAS` finding — a review that skips this section is incomplete.**

### 5.3 Documentation freshness (HM-PROC-02)

Ask: **does this diff make a sentence in `CLAUDE.md` / `README.md` / `docs/` untrue?** A new
entity, endpoint, environment variable, changed flow, or an invalidated documented claim — yes.
A typo, a log message, import ordering, a one-line guard — no. Line count is not the test.

If yes and the doc is not in the diff, report as a non-blocking note:

```
NOT [HM-PROC-02]: CLAUDE.md bu değişiklikle güncellenmedi — {hangi cümle geçersiz kaldı}
```

### 5.4 Collect feeding candidates

This runs whenever Kod Review runs. It **changes nothing**; it only builds a list for ADIM 6.

- **(a) Doc candidate** — the invalidated sentence from 5.3, plus which file and section.
- **(b) New rule candidate** — only if **all four** hold: measurable by grep or a command; the
  next change realistically repeats the mistake; it is broader than one feature; and it is not a
  restatement of an existing HM-* rule (re-read `REAS` and `ARCH` before proposing).
  Fewer than four → it is a doc candidate, not a rule.
- **(c) Stale evidence** — the diff deletes or renames a file, class, or function that a
  `STANDARDS.md` `Evidence:` or `Detect:` line names.
  Check: `grep -rn "{deleted name}" .claude/rules/STANDARDS.md CLAUDE.md`

> A rule added to the rulebook becomes an audit cost on every future commit. The bar is high on
> purpose. A one-off incident is documentation, not a rule.

### 5.5 Report the findings

One block per finding, each with `dosya:satır`:

```
KRİTİK [HM-DATA-02]: alembic/versions/021_foo.py:34
  Sorun: metric_alert tablosu tenant_id ile yaratılıyor ama RLS policy yok
  Düzelt: 020_llm_call_record.py:41-58'deki policy + GRANT bloğunu kopyala

ÖNEMLİ [HM-ARCH-02]: src/humetric/agents/summarizer.py:22
  Sorun: doğrudan sağlayıcı SDK'sı çağrılıyor — retry ve usage kaydı atlanıyor
  Düzelt: structured_call_multi() üzerinden çağır

ÖNERİ [HM-CONV-04]: src/humetric/store.py:412
  Sorun: except Exception, exc loglanmadan yutuluyor
  Düzelt: _log.error("...", exc_info=exc) ekle veya tipi daralt
```

### 5.6 Review rules

- **Make no changes.** Analyse and report only.
- Only changed and added lines. Do not critique surrounding code.
- No praise, no filler. Findings only.
- Nothing found → `Değişiklikler standartlara uygun, sorun tespit edilmedi.`

### 5.7 Summary and verdict

```
Review Özeti
─────────────────────────────────
Değişen dosya: X
KRİTİK: X  → commit bloklanmalı
ÖNEMLİ: X  → düzeltilmesi önerilir
ÖNERİ:  X  → iyileştirme fırsatı
NOT:    X  → bloklamaz

Karar: [COMMIT EDİLEBİLİR / DÜZELTME GEREKLİ / BLOKLANMALI]
```

- KRİTİK = 0 and ÖNEMLİ = 0 → **COMMIT EDİLEBİLİR**
- KRİTİK = 0 and ÖNEMLİ > 0 → **DÜZELTME GEREKLİ** (the user decides)
- KRİTİK > 0 → **BLOKLANMALI**

---

## ADIM 6 — Hafıza & Standart Besleme

> Runs whenever ADIM 5.4 produced at least one candidate — otherwise skip silently.

Present at most 4 candidates in a single `AskUserQuestion` (`multiSelect: true`), each with a
one-line summary. If there are more, list the remainder in the report with
`ayrıca X aday daha var, ayrı çalıştırmada değerlendirin`.

**Nothing is written without approval.** For each approved item:

- **Doc update** — edit only the affected section. Every claim needs evidence (`dosya:satır`);
  never write filler. Unknown → `{bilinmiyor}`. Unverified → `{doğrulanmadı}`.
- **New rule** — re-verify all four conditions from 5.4(b) first. Add it to the right category in
  `.claude/rules/STANDARDS.md` with the next unused number
  (`grep -n '^### HM-' .claude/rules/STANDARDS.md`; numbers are never reused). Fill every field:
  Severity · Scope · Rule · Why · Detect · ❌/✅ · Exception · Evidence (from this real diff).
- **Stale evidence** — do not delete the line. Append
  `**Kaldırıldı ({bugünün tarihi}) — {gerekçe}**` and keep the rule code.

After each write show: old → new → one line of rationale.

---

## ADIM 7 — Summary report

Show only the modules that ran.

```
Pre-Commit Kontrol Raporu
═════════════════════════════════

Otomatik Kapılar:              ← yalnızca seçildiyse
  py_compile / ruff / pytest / alembic durumu

Migration Güvenliği:           ← yalnızca seçildiyse
  Migration: X | KRİTİK: X | YÜKSEK: X | DİKKAT: X
  Aksiyon: [Düzenlendi / Backup üretildi / Rapor / Atlandı]

Kod Review:                    ← yalnızca seçildiyse
  KRİTİK: X | ÖNEMLİ: X | ÖNERİ: X | NOT: X
  Karar: [COMMIT EDİLEBİLİR / DÜZELTME GEREKLİ / BLOKLANMALI]

Besleme:                       ← yalnızca aday bulunduysa
  Aday: X | Uygulanan: X | Atlanan: X

─────────────────────────────────
Genel: [Commit önerilir / Düzeltme sonrası commit önerilir / Commit önerilmez]
```

- Everything clean → `Commit önerilir`
- Any KRİTİK, or a migration KRİTİK/YÜKSEK → `Commit önerilmez`
- Only ÖNEMLİ / ÖNERİ → `Düzeltme sonrası commit önerilir`

---

## ADIM 8 — Draft the commit message

> Runs only if selected. If not selected, skip entirely — including reading the diff for it.

> **Never infer the change from file or directory names.** The message comes from the actual
> diff content and nothing else.

Reuse the diff from ADIM 5. If Kod Review did not run, read it now:

```bash
git diff HEAD
git log --oneline -5
```

Read untracked files directly — they do not appear in `git diff`.

**Use the type and scope tables in `.claude/commands/commit.md`** — they are not duplicated here.

Format: English, Conventional Commits, imperative mood, no capital after the colon, no trailing
period, subject **≤72 characters**. Body only when the motivation is non-obvious, wrapped at 72.
`BREAKING CHANGE:` footer when a public API contract changes.

Present the draft and stop:

```
Önerilen commit mesajı:

feat(store): add tenant key rotation endpoint

Expose PUT /v1/tenant/rotate-api-key so tenants can invalidate their
current key without contacting support.
```

Then: `Commit atmak için /commit çalıştırın.`

---

## General rules

1. **Do nothing for unselected modules** — no diff reading, no file reading, no commands.
2. **Never read the same diff twice.** Reuse what ADIM 1 and ADIM 5 already read.
3. **Never commit. Never push.** This command only analyses.
4. **Report in Turkish; draft the commit message in English.**
5. **Never guess a change from a filename.** Always read the actual diff.
6. **Feeding is always opt-in.** Detection is automatic; writing a file requires the user to
   select that item in `AskUserQuestion`.
