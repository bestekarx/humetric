# LOCAL_DB — Lokal veritabanlarına doğrudan sorgu

> "Localden şunu getir" / "lokal DB'ye bak" dendiğinde **bu dosyadaki bağlantı
> yöntemleri** kullanılır; sorgu doğrudan atılır.
>
> ⚠️ Repoya commit edilir: buraya **gerçek veri, e-posta, API key, token, prod
> bağlantı bilgisi yazma**. Sadece lokal geliştirme bağlantı yöntemi.

Lokalde **iki ayrı** veritabanı var; hangisinin sorulduğuna dikkat et:

| # | Veritabanı | Nerede | Ne tutar |
|---|---|---|---|
| 1 | PostgreSQL 16 + pgvector | `localhost:5433/humetric` | humetric çekirdeği (14 tablo): `tenant`, `api_key`, `entity`, `entity_metric`, `entity_metric_history`, `signal`, `task`, `metric_pack`, `consent`, `usage_record`, `metering_record`, `llm_call_record`, `audit_log`, `user_export` |
| 2 | PostgreSQL (ayrı instance) | `DATABASE_URL` → `../humetric-site/backend/.env` | site (12 tablo): `users`, `api_keys`, `credit_ledger`, `waitlist`, `wizard_runs`, `password_resets`, `agent_sessions`, `agent_events`, `llm_token_usage`, `tenant_memory`, `entity_shares`, `entity_type_shares` |

İkisi `users.tenant_id` ↔ `tenant.id` üzerinden eşleşir ama **aralarında FK yoktur**;
biri sıfırlanırsa yetim (orphan) kayıtlar oluşur.

> ⚠️ **Site artık SQLite kullanmıyor.** `humetric-site/data/humetric.db*` dosyaları
> geçiş öncesinden kalan **ölü kalıntılardır** — hiçbir kod yolu onları açmıyor
> (`backend/src/db.ts` `pg.Pool` ile `DATABASE_URL`'e bağlanır). Oradan okunan veri
> aylar öncesine ait; güncel site verisi için bölüm 2'ye bak.

## Hangi port

Motor Postgres'i **iki farklı** kurulumda farklı porttan dinler. Kanonik liste
[`LOCAL_RUN.md`](LOCAL_RUN.md#portlar) içindedir; buradaki komutlar portu
`PGPORT`'tan okur, hiçbir yere gömmez:

```bash
# Docker'sız (Homebrew postgresql@16) — varsayılan
export PGPORT=5433
# Docker Compose kullanıyorsan
export PGPORT=5434
```

Emin değilsen `.env` içindeki `DATABASE_URL`'in gösterdiği porta bak; bu komutlar
onunla aynı olmalı.

## 1. PostgreSQL (humetric)

Lokal `postgresql@16` trust auth ile çalışıyor — şifre sorulmaz.

```bash
HM="-h 127.0.0.1 -p ${PGPORT:-5433} -U humetric -d humetric"

# tek sorgu
psql $HM -c "select id, code, name, email from tenant order by id"

# sadece değer (script/pipe için)
psql $HM -Atc "select count(*) from signal"

# tablo listesi / şema
psql $HM -c "\dt"
psql $HM -c "\d tenant"

# çok satırlı sorgu
psql $HM <<'SQL'
select t.id, t.code, count(e.id) as entities
from tenant t left join entity e on e.tenant_id = t.id
group by 1,2 order by 1;
SQL
```

**RLS notu:** `humetric` rolü SUPERUSER olduğu için Row-Level Security **bypass**
edilir → tüm tenant'ların satırları görünür. Uygulamanın gerçekte ne gördüğünü
test etmek istiyorsan kısıtlı rolle bağlan ve tenant context'i set et:

```bash
PGPASSWORD=humetric psql -h 127.0.0.1 -p "${PGPORT:-5433}" -U humetric_app -d humetric <<'SQL'
select set_config('app.tenant_id', '1', false);
select id, name from entity;   -- yalnızca tenant 1
SQL
```

Context set edilmezse **sıfır satır** döner (fail-closed) — bu bir hata değil,
tasarım gereği.

## 2. PostgreSQL (site)

Bağlantı bilgisi **`../humetric-site/backend/.env`** içindeki `DATABASE_URL`'dedir
(gitignored — buraya yazma). Kabuğa yükleyip kullan:

```bash
export $(grep -E '^DATABASE_URL=' ../humetric-site/backend/.env | xargs)

psql "$DATABASE_URL" -c "\dt"
psql "$DATABASE_URL" -c "\d users"

# okunur çıktı
psql "$DATABASE_URL" -c "select id, email, name, created_at, tenant_id, email_verified from users order by id"

# tek değer (script/pipe için)
psql "$DATABASE_URL" -Atc "select count(*) from users"
```

Dikkat: site şeması Alembic'le değil, `backend/schema.sql` + boot'ta çalışan
`runStartupTasks()` ile **elle** senkron tutulur. Bir kolon beklediğin yerde yoksa
önce bu ikisinin ayrışıp ayrışmadığına bak.

Site tarafında RLS yok; tüm satırlar görünür.

## Sık kullanılan sorgular

`$HM` ve `$DATABASE_URL` yukarıdaki bölümlerde tanımlanıyor.

```bash
# Bir e-posta iki tarafta da var mı?
psql "$DATABASE_URL" \
  -c "select id, email, name, tenant_id, email_verified from users where email ilike '%ARANAN%'"
psql $HM -c "select id, code, name, email, created_at from tenant where email ilike '%ARANAN%'"

# Yetim site kullanıcıları (motorda karşılığı olmayan tenant_id'ler)
psql $HM -Atc "select id from tenant order by id"
# ^ çıkan id listesiyle site'taki users.tenant_id değerlerini karşılaştır

# Kuyruk durumu
psql $HM -c "select status, count(*) from task group by 1 order by 1"

# Kuyrukta bekleyen en eski iş (worker takıldı mı?)
psql $HM -c "select id, task_type, retry_count, created_at from task where status='queued' order by created_at limit 5"

# Son sinyaller
psql $HM -c "select id, tenant_id, status, created_at from signal order by created_at desc limit 10"

# İncelemeyi bekleyen metrikler
psql $HM -c "select entity_id, metric_key, value, confidence from entity_metric where review_status='pending_review' limit 20"

# Site: kredi bakiyesi ve son hareketler
psql "$DATABASE_URL" -c "select user_id, delta_cents, reason, balance_after_cents, created_at from credit_ledger order by id desc limit 10"

# Site: ajan oturumları
psql "$DATABASE_URL" -c "select id, kind, status, turn_count, cost_cents from agent_sessions order by created_at desc limit 10"
```

## Kurallar

- **Yazma işlemi (UPDATE/DELETE/DROP) önce kullanıcıya sorulur.** Okuma serbest.
- Port için tek kaynak [`LOCAL_RUN.md`](LOCAL_RUN.md#portlar); burada `PGPORT`
  kullanılır, sayı gömülmez.
- Ayağa kaldırma adımları için bkz. [`LOCAL_RUN.md`](LOCAL_RUN.md).
- Sorgu sonuçları lokal test verisidir; **commit'e, dokümana veya dışarıya taşıma**.
