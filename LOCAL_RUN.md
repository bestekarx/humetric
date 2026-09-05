# LOCAL_RUN — Docker'sız lokal çalıştırma (tam stack)

> "Çalıştır" / "lokalde çalıştır" dendiğinde **bu dosyadaki adımlar** uygulanır.
> Docker Desktop gerekmez — Homebrew PostgreSQL 16 + pgvector kullanılır.
>
> ⚠️ Bu dosya repoya commit edilir: buraya **hiçbir** API key, şifre, sunucu
> IP'si veya kişisel yol yazma. Sırlar `.env` (gitignored) içinde kalır.

## Neden Docker'sız

`docker-compose.yml` pgvector'ü **5434** portunda ayağa kaldırır. Docker kapalıyken
aynı işi makinede kurulu `postgresql@16` (port **5433**) + `pgvector` uzantısı görür.
Bu cluster'da başka projelerin veritabanları da olabilir; sadece `humetric`
veritabanı oluşturulur, diğerlerine dokunulmaz.

## Ön koşullar (tek seferlik)

```bash
brew install postgresql@16 pgvector   # kurulu değilse
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                  # ANTHROPIC_API_KEY, VOYAGE_API_KEY, HUMETRIC_AUTH_SECRET doldur
```

PostgreSQL'in dinlediği portu doğrula (bu dosyada varsayılan **5433**):

```bash
lsof -nP -iTCP -sTCP:LISTEN | grep postgres
```

## 1. Veritabanı hazırlığı (tek seferlik)

```bash
PGPORT=5433
psql -h 127.0.0.1 -p $PGPORT -d postgres -v ON_ERROR_STOP=1 <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='humetric') THEN
    CREATE ROLE humetric WITH LOGIN SUPERUSER PASSWORD 'humetric';
  END IF;
END $$;
SQL

psql -h 127.0.0.1 -p $PGPORT -d postgres -Atc \
  "SELECT 1 FROM pg_database WHERE datname='humetric'" | grep -q 1 \
  || createdb -h 127.0.0.1 -p $PGPORT -O humetric humetric

psql -h 127.0.0.1 -p $PGPORT -U humetric -d humetric -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

`SUPERUSER` sadece lokal geliştirme içindir — `alembic` migration 001'in
`humetric_app` rolünü oluşturabilmesi gerekir. Prod'da asla böyle yapma.

## 2. `.env` portunu 5433'e çevir

`.env` (gitignored) içinde iki satır:

```
DATABASE_URL=postgresql+psycopg://humetric:humetric@localhost:5433/humetric
DATABASE_URL_APP=postgresql+psycopg://humetric_app:humetric_app@localhost:5433/humetric
```

> Docker'a geri dönersen bu portları `5434` yap. Port tablosu:
> [Portlar](#portlar) — `LOCAL_DB.md` de oraya bakar, sayıyı iki yerde tutmayalım.

### Site'ın veritabanı ayrı

`humetric-site` **kendi PostgreSQL'ini** kullanır (SQLite değil — repodaki
`data/humetric.db*` ölü kalıntıdır). `humetric-site/backend/.env` içinde ayrı bir
`DATABASE_URL` gerekir; aynı cluster'da ikinci bir veritabanı yeterlidir:

```bash
createdb -h 127.0.0.1 -p $PGPORT -O humetric humetric_site
psql -h 127.0.0.1 -p $PGPORT -U humetric -d humetric_site -f ../humetric-site/backend/schema.sql
```

Şema Alembic'le yönetilmez: `schema.sql` elle uygulanır, kalan farkları backend
her boot'ta `runStartupTasks()` ile kapatır.

## 3. Migration + seed (tek seferlik)

```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m humetric.seed --tenant default --name "Default Tenant" --api-key admin
```

Seed çıktısındaki `hm_live_...` anahtarını not al — bir daha gösterilmez.

## 4. Servisleri başlat (her seferinde)

Dört süreç, ayrı ayrı arka planda:

```bash
# humetric API  → :8002
.venv/bin/uvicorn humetric.api:app --port 8002 --host 127.0.0.1

# humetric worker (Postgres task queue)
.venv/bin/python -m humetric.worker

# site backend (Express + kendi Postgres'i) → :3001
npm --prefix ../humetric-site/backend run dev

# site frontend (Vite) → :5173
npm --prefix ../humetric-site/frontend run dev
```

> `humetric-site` kökündeki `npm run dev` **çalışmaz** (root `node_modules` yok,
> `concurrently: command not found`). Backend ve frontend'i yukarıdaki gibi ayrı
> ayrı başlat, ya da bir kez `npm install --prefix ../humetric-site` çalıştır.

## 5. Doğrulama

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8002/health   # 401 = ayakta (auth ister)
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3001/         # 200
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5173/         # 200

# uçtan uca: site backend → humetric API
curl -s -X POST http://localhost:3001/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","name":"Dev","password":"Test12345!"}'
```

Son komut `token` + `user` döndürüyorsa zincir tamamdır (humetric API log'unda
`POST /v1/register 201` ve `POST /v1/login 200` görünür).

Tarayıcı: **http://localhost:5173**

## Portlar

| Servis | Port | Not |
|---|---|---|
| PostgreSQL (brew, pgvector) | 5433 | Docker kullanılırsa 5434 |
| humetric API | 8002 | `/health` dahil tüm uçlar auth ister |
| humetric worker | — | port dinlemez |
| Site backend (Express) | 3001 | `HUMETRIC_API_URL=http://localhost:8002`, kendi `DATABASE_URL`'i |
| Site frontend (Vite) | 5173 | `/api` → 3001 proxy |

## Site'ı canlı API'ye bağlamak

Varsayılan lokal API'dir. Canlıya bağlamak için `humetric-site/backend/.env`
içindeki `HUMETRIC_API_URL` değerini canlı API adresiyle değiştir — o zaman
lokal Postgres/API/worker gerekmez, ama **gerçek prod verisiyle** çalışırsın.

## Sık karşılaşılan hatalar

- `connection refused ... 5434` → `.env` hâlâ Docker portunu gösteriyor, 5433 yap.
- `role "humetric_app" does not exist` → `alembic upgrade head` çalıştırılmamış.
- `extension "vector" is not available` → `brew install pgvector`, sonra
  `CREATE EXTENSION vector;` (adım 1).
- Pipeline (signal → metric) 401 veriyorsa `.env` içindeki `ANTHROPIC_API_KEY` /
  `VOYAGE_API_KEY` geçersizdir. Site, auth ve key yönetimi bunlarsız da çalışır.
