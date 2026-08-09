# LOCAL_DB — Lokal veritabanlarına doğrudan sorgu

> "Localden şunu getir" / "lokal DB'ye bak" dendiğinde **bu dosyadaki bağlantı
> yöntemleri** kullanılır; sorgu doğrudan atılır.
>
> ⚠️ Repoya commit edilir: buraya **gerçek veri, e-posta, API key, token, prod
> bağlantı bilgisi yazma**. Sadece lokal geliştirme bağlantı yöntemi.

Lokalde **iki ayrı** veritabanı var; hangisinin sorulduğuna dikkat et:

| # | Veritabanı | Nerede | Ne tutar |
|---|---|---|---|
| 1 | PostgreSQL 16 + pgvector | `localhost:5433/humetric` | humetric çekirdeği: `tenant`, `api_key`, `entity`, `entity_metric`, `entity_metric_history`, `signal`, `task`, `metric_pack`, `consent`, `usage_record`, `metering_record`, `audit_log` |
| 2 | SQLite | `../humetric-site/data/humetric.db` | site kullanıcıları: `users`, `api_keys`, `password_resets`, `waitlist`, `wizard_runs` |

İkisi `users.tenant_id` ↔ `tenant.id` üzerinden eşleşir ama **aralarında FK yoktur**;
biri sıfırlanırsa yetim (orphan) kayıtlar oluşur.

## 1. PostgreSQL (humetric)

Lokal `postgresql@16` trust auth ile çalışıyor — şifre sorulmaz.

```bash
# tek sorgu
psql -h 127.0.0.1 -p 5433 -U humetric -d humetric -c "select id, code, name, email from tenant order by id"

# sadece değer (script/pipe için)
psql -h 127.0.0.1 -p 5433 -U humetric -d humetric -Atc "select count(*) from signal"

# tablo listesi / şema
psql -h 127.0.0.1 -p 5433 -U humetric -d humetric -c "\dt"
psql -h 127.0.0.1 -p 5433 -U humetric -d humetric -c "\d tenant"

# çok satırlı sorgu
psql -h 127.0.0.1 -p 5433 -U humetric -d humetric <<'SQL'
select t.id, t.code, count(e.id) as entities
from tenant t left join entity e on e.tenant_id = t.id
group by 1,2 order by 1;
SQL
```

**RLS notu:** `humetric` rolü SUPERUSER olduğu için Row-Level Security **bypass**
edilir → tüm tenant'ların satırları görünür. Uygulamanın gerçekte ne gördüğünü
test etmek istiyorsan kısıtlı rolle bağlan ve tenant context'i set et:

```bash
PGPASSWORD=humetric psql -h 127.0.0.1 -p 5433 -U humetric_app -d humetric <<'SQL'
select set_config('app.tenant_id', '1', false);
select id, name from entity;   -- yalnızca tenant 1
SQL
```

Context set edilmezse **sıfır satır** döner (fail-closed) — bu bir hata değil,
tasarım gereği.

## 2. SQLite (site)

```bash
DB=../humetric-site/data/humetric.db

sqlite3 "$DB" ".tables"
sqlite3 "$DB" ".schema users"

# okunur çıktı
sqlite3 -header -column "$DB" "select id, email, name, created_at, tenant_id, email_verified from users order by id"

# tek değer
sqlite3 "$DB" "select count(*) from users"
```

`humetric.db-wal` / `-shm` dosyaları WAL modundan gelir; site backend çalışırken de
okumak güvenlidir.

## Sık kullanılan sorgular

```bash
# Bir e-posta iki tarafta da var mı?
sqlite3 -header -column ../humetric-site/data/humetric.db \
  "select id,email,name,tenant_id,email_verified from users where email like '%ARANAN%'"
psql -h 127.0.0.1 -p 5433 -U humetric -d humetric \
  -c "select id, code, name, email, created_at from tenant where email ilike '%ARANAN%'"

# Yetim site kullanıcıları (Postgres'te karşılığı olmayan tenant_id'ler)
psql -h 127.0.0.1 -p 5433 -U humetric -d humetric -Atc "select id from tenant order by id"
# ^ çıkan id listesiyle SQLite'taki users.tenant_id değerlerini karşılaştır

# Kuyruk durumu
psql -h 127.0.0.1 -p 5433 -U humetric -d humetric \
  -c "select status, count(*) from task group by 1 order by 1"

# Son sinyaller
psql -h 127.0.0.1 -p 5433 -U humetric -d humetric \
  -c "select id, tenant_id, created_at from signal order by id desc limit 10"
```

## Kurallar

- **Yazma işlemi (UPDATE/DELETE/DROP) önce kullanıcıya sorulur.** Okuma serbest.
- Bağlantı bilgisi `.env` (gitignored) ile aynı olmalı: `DATABASE_URL` **5433**
  gösteriyorsa buradaki komutlar geçerlidir. Docker'a dönülürse port **5434** olur.
- Ayağa kaldırma adımları için bkz. [`LOCAL_RUN.md`](LOCAL_RUN.md).
- Sorgu sonuçları lokal test verisidir; **commit'e, dokümana veya dışarıya taşıma**.
