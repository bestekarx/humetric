# Localde Çalıştırma Rehberi

Bu doküman **iki ayrı repoyu** birlikte localde ayağa kaldırmayı anlatır:

- **`humetric`** (bu repo) — açık kaynak backend API + worker. UI/site içermez.
- **`humetric-site`** (`/Users/bestekarx/RiderProjects/humetric-site`) — ayrı
  proje, dashboard/website. Node/Express backend + React/Vite frontend.
  humetric'e HTTP üzerinden (`/v1/*`) bağlanır.

Site kodu bu repoda yaşamaz; değişiklikler `humetric-site` projesinde yapılır.
Detay için `CLAUDE.md`'deki proje ayrımı notuna bakın.

## Mimari (local)

```
┌─────────────────┐     :5173      ┌──────────────────┐     :8002     ┌─────────────────┐
│  site frontend   │ ───────────▶  │   site backend    │ ───────────▶ │  humetric API    │
│  (Vite/React)    │  /api proxy   │  (Express, :3001)  │   /v1/*      │  (FastAPI)       │
└─────────────────┘                └──────────────────┘               └────────┬────────┘
                                    SQLite (kullanıcı/JWT)                       │
                                                                                  ▼
                                                                        ┌─────────────────┐
                                                                        │ humetric worker  │
                                                                        │ (Postgres kuyruğu)│
                                                                        └────────┬────────┘
                                                                                  ▼
                                                                        ┌─────────────────┐
                                                                        │ PostgreSQL+pgvector│
                                                                        │  Docker, :5434    │
                                                                        └─────────────────┘
```

Not: Redis/broker yok — worker, Postgres tablosunu `SELECT FOR UPDATE SKIP LOCKED`
ile kuyruk gibi kullanır.

## Gereksinimler

- Docker Desktop (pgvector container için)
- Python 3.11+ (bu repoda `.venv` zaten kurulu: `pip install -e ".[dev]"`)
- Node.js 20+ (`humetric-site` için)
- `humetric/.env` dosyasında: `DATABASE_URL`, `DATABASE_URL_APP`,
  `HUMETRIC_AUTH_SECRET`, `HUMETRIC_ENCRYPTION_KEY`, `ANTHROPIC_API_KEY`,
  `VOYAGE_API_KEY` set edilmiş olmalı (bkz. `.env.example`).
- `humetric-site/backend/.env` dosyasında: `JWT_SECRET`, `PORT=3001`,
  `HUMETRIC_API_URL=http://localhost:8002`, `ENCRYPTION_KEY`.

## Adım adım başlatma

### 1. Docker Desktop

```bash
open -a Docker
# hazır olana kadar bekle: docker info
```

### 2. Veritabanı (pgvector, port 5434)

```bash
cd /Users/bestekarx/RiderProjects/humetric
docker compose up -d db
```

Sadece `db` servisini başlatır — `api`/`worker` container'ları başlatılmaz,
onlar hostta çalışır (adım 3-4). Healthcheck `healthy` olana kadar bekleyin:

```bash
docker inspect --format '{{.State.Health.Status}}' humetric-db-1
```

### 3. Migration + seed (ilk kurulumda / DB sıfırlandığında)

```bash
source .venv/bin/activate
alembic upgrade head
python -m humetric.seed --tenant default --name "Default Tenant" --api-key admin
```

`alembic upgrade head` migration 001'de `vector` + `pg_trgm` extension'larını ve
`humetric_app` rolünü de oluşturur. Seed idempotent — tenant zaten varsa
bildirir.

### 4. humetric API (host, hot-reload)

```bash
cd /Users/bestekarx/RiderProjects/humetric
.venv/bin/uvicorn humetric.api:app --host 0.0.0.0 --port 8002 --reload
```

Doğrulama: `curl http://localhost:8002/healthz` → `{"status":"ok",...}`
Swagger: http://localhost:8002/docs

### 5. humetric worker (host, ayrı terminal)

```bash
cd /Users/bestekarx/RiderProjects/humetric
.venv/bin/python -m humetric.worker
```

Signal → metrik pipeline'ını işler (extractor → curator, Anthropic + Voyage
çağrıları). Heartbeat dosyası: `/tmp/humetric_worker_heartbeat`.

### 6. humetric-site backend (Express, port 3001)

```bash
cd /Users/bestekarx/RiderProjects/humetric-site
npm run dev --prefix backend
```

`backend/.env`'deki `HUMETRIC_API_URL=http://localhost:8002` üzerinden
humetric API'ye proxy yapar (`/register`, `/login`, `/api-keys`,
`/tenant/dashboard`, `/tenant/keys`).

### 7. humetric-site frontend (Vite, port 5173)

```bash
cd /Users/bestekarx/RiderProjects/humetric-site
npm run dev --prefix frontend
```

Tarayıcıda aç: **http://localhost:5173**
(Vite `/api` isteklerini `:3001`'e proxy'ler.)

## Hızlı sağlık kontrolü

```bash
docker inspect --format '{{.State.Health.Status}}' humetric-db-1   # healthy
curl -s http://localhost:8002/healthz                                # {"status":"ok",...}
pgrep -f 'humetric.worker' && echo "worker running"
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5173/     # 200
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3001/api/auth/login \
  -X POST -H 'Content-Type: application/json' -d '{}'               # 400 (boş body -> validasyon hatası, normal)
```

## Uçtan uca test (gerçek LLM pipeline'ı dahil)

Repo, register → login → API key → LLM pack wizard → signal → worker → metrics
→ query akışını baştan sona sınayan bir smoke test harness'i içerir:

```bash
cd /Users/bestekarx/RiderProjects/humetric
.venv/bin/python -m humetric_test --scenario beta_smoke --base-url http://localhost:8002/v1 --verbose
```

Başarılı bir koşu tüm adımlarda `[PASS]` ve `0 failed` gösterir; log'lar
`logs/beta_smoke_*.json`'a yazılır.

## Sık karşılaşılan sorunlar

- **`ANTHROPIC_API_KEY`/`VOYAGE_API_KEY` geçersiz** → worker log'unda
  `anthropic.AuthenticationError: ... invalid x-api-key`. Site/kayıt/login/API
  key yönetimi bu anahtarlar olmadan da çalışır; sadece signal→metrik
  çıkarımı (LLM/embedding) etkilenir. Anahtarı `.env`'de düzeltip API + worker'ı
  yeniden başlatın.
- **Docker daemon kapalı** → `docker compose up -d db` "Cannot connect to the
  Docker daemon" hatası verir. `open -a Docker` ile başlatıp `docker info`
  komutunun hata vermediğini doğrulayın.
- **Port çakışması** → `lsof -i :8002` / `:3001` / `:5173` / `:5434` ile hangi
  process'in tuttuğunu kontrol edin.
- **`landing/` veya statik site aramayın** — humetric'te artık site kodu yok;
  tüm UI `humetric-site` projesindedir.
