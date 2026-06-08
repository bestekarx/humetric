# Turkish → English Rename Plan

**Goal:** Eradicate every Turkish identifier from the codebase — DB columns, enum values,
schema fields, env vars, internal variable names. No Turkish must remain anywhere.

**Version bump:** `0.1.0` → `1.0.0` (breaking change — DB schema + API contract change)

---

## ⚠️ CRITICAL BUG (fix first, same session)

`alembic upgrade head` currently does NOT produce a schema that matches `models.py`.
Migration 001+002 are massively out of sync:

| Missing from migrations | Impact |
|---|---|
| Tables: `metering_record`, `consent`, `audit_log`, `metric_pack`, `task` | App crashes on billing, KVKK, pack queries |
| `Tenant` missing 15 columns: `durum`, `kota_*`, `embedding_provider`, `llm_provider`, `anthropic_key_encrypted`, `voyage_key_encrypted`, `email`, `email_verified`, `password_hash`, `stripe_*`, `subscription_*`, `tier`, `updated_at` | Virtually everything fails |
| `Entity` missing `embedding_pending` column | Worker crashes |

**Fix strategy:** Completely rewrite `001_initial_schema.py` to reflect the **full, correct,
English-named** schema. Since no one has deployed yet, there are no existing databases to
migrate — a clean rewrite of the initial migration is the right call.

---

## Rename Map

### A. DB Columns (models.py + migration rewrite)

| Table | Old name | New name | Notes |
|---|---|---|---|
| `tenant` | `kod` | `code` | UniqueConstraint `uq_tenant_kod` → `uq_tenant_code` |
| `tenant` | `ad` | `name` | |
| `tenant` | `durum` | `status` | server_default `"aktif"` → `"active"` |
| `tenant` | `kota_sinyal_aylik` | `monthly_signal_quota` | |
| `tenant` | `kota_entity` | `entity_quota` | |
| `entity` | `embedding_metni` | `embedding_text` | |
| `metering_record` | `tarih` | `date` | UniqueConstraint `uq_metering_tenant_tarih` → `uq_metering_tenant_date` |
| `metering_record` | `sinyal_sayisi` | `signal_count` | |
| `metering_record` | `llm_token_sayisi` | `llm_token_count` | |
| `metering_record` | `embedding_sayisi` | `embedding_count` | |

### B. TenantStatus Enum (schema.py + models.py server_default)

| Old | New | DB server_default |
|---|---|---|
| `TenantStatus.aktif = "aktif"` | `TenantStatus.active = "active"` | `"active"` |
| `TenantStatus.pasif = "pasif"` | `TenantStatus.inactive = "inactive"` | |
| `TenantStatus.askida = "askida"` | `TenantStatus.suspended = "suspended"` | |

### C. Pydantic Schema Fields (schema.py — public API contract)

| Class | Old field | New field |
|---|---|---|
| `TenantCreate` | `kod` | `code` |
| `TenantCreate` | `ad` | `name` |
| `TenantCreate` | `durum` | `status` |
| `TenantCreate` | `kota_sinyal_aylik` | `monthly_signal_quota` |
| `TenantCreate` | `kota_entity` | `entity_quota` |
| `TenantRead` | `kod` | `code` |
| `TenantRead` | `ad` | `name` |
| `TenantRead` | `durum` | `status` |
| `TenantRead` | `kota_sinyal_aylik` | `monthly_signal_quota` |
| `TenantRead` | `kota_entity` | `entity_quota` |
| `UsageRecordOut` | `tarih` | `date` |
| `UsageRecordOut` | `sinyal_sayisi` | `signal_count` |
| `UsageRecordOut` | `llm_token_sayisi` | `llm_token_count` |
| `UsageRecordOut` | `embedding_sayisi` | `embedding_count` |
| `UsageReportResponse` | `baslangic` | `start_date` |
| `UsageReportResponse` | `bitis` | `end_date` |
| `UsageReportResponse` | `toplam` | `total` |

### D. Config / Env Var (config.py + .env.example + README)

| Old | New |
|---|---|
| `GUVEN_ESIGI` (Python var) | `CONFIDENCE_THRESHOLD` |
| `HUMETRIC_GUVEN_ESIGI` (env var) | `HUMETRIC_CONFIDENCE_THRESHOLD` |

---

## Step-by-Step Execution

### Step 1 — models.py

Apply every rename from section A and B above. Also:
- `Tenant.durum` server_default `"aktif"` → `"active"`
- `UniqueConstraint("kod", name="uq_tenant_kod")` → `UniqueConstraint("code", name="uq_tenant_code")`
- `UniqueConstraint("tenant_id", "tarih", name="uq_metering_tenant_tarih")` → `UniqueConstraint("tenant_id", "date", name="uq_metering_tenant_date")`

### Step 2 — schema.py

- Rename `TenantStatus` enum attrs + string values (section B).
- Rename all Pydantic fields listed in section C.
- `TenantCreate.durum: TenantStatus = TenantStatus.aktif` → `status: TenantStatus = TenantStatus.active`
- `UsageReportResponse.baslangic`, `.bitis`, `.toplam` (section C).

### Step 3 — config.py

```python
# Before
GUVEN_ESIGI = float(os.environ.get("HUMETRIC_GUVEN_ESIGI", "0.55"))

# After
CONFIDENCE_THRESHOLD = float(os.environ.get("HUMETRIC_CONFIDENCE_THRESHOLD", "0.55"))
```

### Step 4 — Code references (4 files)

**store.py** — 2 occurrences:
```python
# line 35
Tenant.kod == kod  →  Tenant.code == code
# line 686
entity.embedding_metni = embed_text  →  entity.embedding_text = embed_text
```

**agents/curator.py** — 2 occurrences:
```python
config.GUVEN_ESIGI  →  config.CONFIDENCE_THRESHOLD   # lines 87, 94
```

**seed.py** — 1 occurrence:
```python
{"kod": kod, "ad": ad or kod}  →  {"code": kod, "name": ad or kod}
```

**services/usage_service.py** — update:
- `TIER_LIMITS` dict key `"sinyal_sayisi"` → `"signal_count"`
- `_upsert_usage(... tarih: date ...)` → `date_val: date` (rename param to avoid shadowing `date` builtin)
- All `sinyal_sayisi=1` kwargs → `signal_count=1`
- All `llm_token_sayisi=count` → `llm_token_count=count`
- All `embedding_sayisi=1` → `embedding_count=1`
- `r.sinyal_sayisi` etc. attribute accesses → new names
- Function `check_tier_limit` metric key `"sinyal_sayisi"` → `"signal_count"`

**middleware/billing_guard.py** — 3 occurrences:
```python
"/v1/signals": ("sinyal_sayisi", ...)  →  ("signal_count", ...)
metric_key == "sinyal_sayisi"  →  "signal_count"
MeteringRecord.sinyal_sayisi  →  MeteringRecord.signal_count
MeteringRecord.tarih  →  MeteringRecord.date
```

**api.py** — ~14 occurrences across usage-report helpers (lines 1224–1371):
- `MeteringRecord.tarih` → `.date`
- `r.tarih`, `r.sinyal_sayisi`, `r.llm_token_sayisi`, `r.embedding_sayisi` → new names
- Dict keys `"sinyal_sayisi"`, `"llm_token_sayisi"`, `"embedding_sayisi"` → new names
- `UsageRecordOut(tarih=..., sinyal_sayisi=..., ...)` constructor → new field names
- `UsageReportResponse(... baslangic=..., bitis=..., toplam=...)` → new field names
- `_build_usage_report(db, tenant_id, baslangic, bitis)` params → `start_date, end_date`

### Step 5 — Rewrite 001_initial_schema.py (CRITICAL)

The migration must be a **complete, clean rewrite** that:
1. Creates ALL 11 tables matching models.py exactly with English column names
2. Removes Turkish variable names (`tablo` → `table_name`, `rls_tablolar` → `rls_tables`)
3. Removes Turkish print statement (line 147: `"SEED API KEY (default tenant, tum scope'lar): ..."`)
4. Adds all missing columns to `tenant` table
5. Adds `embedding_pending` to `entity` table
6. Adds missing tables: `metering_record`, `consent`, `audit_log`, `metric_pack`, `task`
7. Sets up RLS on all applicable tables

Tables to create (in dependency order):
1. `tenant` — full schema, English column names
2. `api_key`
3. `entity` — with `embedding_text` (was `embedding_metni`), `embedding_pending`
4. `entity_metric`
5. `signal`
6. `usage_record`
7. `metering_record` — with `date`, `signal_count`, `llm_token_count`, `embedding_count`
8. `consent`
9. `audit_log`
10. `metric_pack`
11. `task`

RLS applies to: `api_key`, `entity`, `entity_metric`, `signal`, `usage_record`,
`metering_record`, `consent`, `audit_log`, `metric_pack`, `task`

Migration 002 (`signal`) can be kept as-is (it creates a table already in the new 001, so
it will conflict). **Delete 002_signal_table.py** and move its `signal` table creation into
the new 001. The `down_revision` chain becomes: 001 only.

### Step 6 — .env.example

```
# Before
HUMETRIC_GUVEN_ESIGI=

# After
HUMETRIC_CONFIDENCE_THRESHOLD=0.55
```

### Step 7 — README.md

Update the configuration table row:
```
| `HUMETRIC_GUVEN_ESIGI` | `0.55` | Minimum confidence threshold |
→
| `HUMETRIC_CONFIDENCE_THRESHOLD` | `0.55` | Minimum confidence threshold |
```

### Step 8 — pyproject.toml version bump

```toml
version = "0.1.0"  →  version = "1.0.0"
```

### Step 9 — Verify

```bash
# Syntax check
find src/humetric -name "*.py" | xargs python3 -m py_compile && echo "OK"

# No Turkish remaining
grep -rn "kod\b\|\.ad\b\|durum\|aktif\|pasif\|askida\|tarih\|sinyal_sayisi\|llm_token_sayisi\|embedding_sayisi\|embedding_metni\|GUVEN_ESIGI\|baslangic\|bitis\|toplam\|kota_" \
  src/humetric --include="*.py" \
  | grep -v "migrations/versions/.*\.pyc"

# Migration smoke test (requires Docker stack)
docker compose up -d
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

### Step 10 — Commit

```
feat(migrations)!: rename all Turkish DB columns and API fields to English

BREAKING CHANGE: DB schema, API response fields, and env var renamed.
See docs/rename-plan.md for the full mapping.
```

---

## Files touched (summary)

| File | Change type |
|---|---|
| `src/humetric/db/models.py` | Rename ~10 columns + 2 constraints |
| `src/humetric/schema.py` | Rename enum values + ~17 Pydantic fields |
| `src/humetric/config.py` | Rename 1 var + env key |
| `src/humetric/store.py` | 2 attribute refs |
| `src/humetric/agents/curator.py` | 2 config refs |
| `src/humetric/seed.py` | 1 dict literal |
| `src/humetric/services/usage_service.py` | ~8 kwarg + attr refs |
| `src/humetric/middleware/billing_guard.py` | 3 refs |
| `src/humetric/api.py` | ~14 refs in usage-report section |
| `src/humetric/db/migrations/versions/001_initial_schema.py` | Full rewrite |
| `src/humetric/db/migrations/versions/002_signal_table.py` | Delete (merged into 001) |
| `.env.example` | 1 env var |
| `README.md` | 1 table row |
| `pyproject.toml` | version `0.1.0` → `1.0.0` |
