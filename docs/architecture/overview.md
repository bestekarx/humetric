# Sistem Haritası

> Bu dosya **repo içi mimari referansıdır**, yayınlanan VitePress sitesinin parçası
> değildir (`.vitepress/config.ts` → `srcExclude`). Diyagramlar GitHub'ın native
> Mermaid render'ıyla görüntülenir. İddiaların yanındaki `dosya:satır` referansları
> yazıldığı andaki koda aittir — çelişki görürsen koda güven, dosyayı güncelle.

HuMetric iki ayrı repodan oluşur. Bu repo (`humetric`) **sadece backend motorudur**;
web sitesi ve dashboard ayrı bir projede (`../humetric-site`) yaşar ve motoru HTTP
üzerinden tüketir.

## Topoloji

```mermaid
graph LR
    subgraph client["İstemciler"]
        BR["Tarayıcı<br/>React SPA"]
        MCPC["MCP istemcisi<br/>(Claude Code / Desktop)"]
        API_C["Doğrudan API<br/>(SDK, n8n, curl)"]
    end

    subgraph site["humetric-site &nbsp;(ayrı repo)"]
        EXP["Express :3001<br/>backend/src/index.ts"]
        SMCP["Site MCP /mcp<br/>backend/src/mcpServer/<br/>Pack Wizard + Signal Chat"]
        SPG[("Site Postgres<br/>12 tablo")]
    end

    subgraph engine["humetric &nbsp;(bu repo)"]
        EMCP["Motor MCP<br/>src/humetric/mcp_server.py<br/>stdio · 26 tool"]
        FAPI["FastAPI :8002<br/>src/humetric/api.py"]
        WRK["worker.py<br/>batch_worker.py"]
        PG[("PostgreSQL 16<br/>+ pgvector<br/>14 tablo · RLS")]
    end

    subgraph ext["Dış servisler"]
        LLM["LLM sağlayıcı<br/>BYOK: Anthropic /<br/>OpenAI / Google / DeepSeek"]
        EMB["Embedding sağlayıcı<br/>Voyage / OpenAI / Cohere"]
        STR["Stripe"]
        SMTP["SMTP"]
    end

    BR -->|"/api/*"| EXP
    MCPC -->|"hms_live_ bearer"| SMCP
    MCPC -->|"stdio"| EMCP
    API_C -->|"hm_live bearer"| FAPI

    SMCP --> EXP
    EXP -->|"/v1/* &nbsp;hm_live bearer"| FAPI
    EMCP -->|"/v1/* &nbsp;HTTP"| FAPI
    EXP --> SPG
    SMCP --> SPG

    FAPI --> PG
    WRK --> PG
    WRK --> LLM
    WRK --> EMB
    FAPI --> STR
    FAPI --> SMTP
    SMCP --> LLM
```

**Kritik nokta:** motor MCP'si veritabanına *hiç* dokunmaz — REST API'nin saf HTTP
istemcisidir ve `humetric` paketinden hiçbir şey import etmez
(`mcp_server.py:68-70`). Site MCP'si ise site Postgres'ine doğrudan yazar.

## Bileşen sorumlulukları

| Bileşen | Sorumluluk | Sınır |
|---|---|---|
| `api.py` (FastAPI :8002) | Tüm HTTP route'ları, senkron doğrulama, kuyruğa iş bırakma | LLM çağırmaz (tek istisna: `/v1/query` re-rank ve `/v1/packs/wizard`) |
| `worker.py` | Uzun ömürlü kuyruk tüketicisi, tüm task tipleri, zamanlayıcılar | Tek LLM çağrısı: extraction |
| `batch_worker.py` | Tek seferlik backfill; Anthropic Batches API (%50 maliyet) | Yalnızca `signal_process` |
| `store.py` | Tüm veri erişimi (SQLAlchemy 2.0 async) | Route handler'ı SQL yazmaz |
| `agents/` | LLM'e giden yapılandırılmış çağrılar | `curator` **LLM değil** — deterministik Python |
| `humetric-site` Express | Oturum, BYOK anahtar saklama, kredi, paylaşım linkleri, ajan oturumları | Motor verisini kendi DB'sine kopyalamaz |
| Site MCP | Pack Wizard + Signal Chat ajanları, kredi düşme | Motor DB'sine erişmez, `/v1/*` üzerinden gider |

## İki repo neden ayrı

`humetric` açık kaynak backend'dir; site/dashboard kapalı ve ayrı deploy edilir.
Pratik sonuçları:

- Site/frontend değişikliği bu repoya **girmez** (`CLAUDE.md` açılış bloğu).
- Kimlik iki tarafta ayrı yaşar: site `users` tablosu ↔ motor `tenant` tablosu
  `users.tenant_id` ile eşleşir ama **aralarında FK yoktur**; biri sıfırlanırsa
  yetim kayıt oluşur.
- Motor tarafında "kullanıcı" diye ayrı bir varlık yok: **bir kullanıcı = bir tenant**.
  Ayrıntı: [`data-model.md`](data-model.md#hafıza-katmanları).

## Portlar

| Servis | Port | Kaynak |
|---|---|---|
| Motor API | 8002 | `uvicorn humetric.api:app --port 8002` |
| Site Express | 3001 | `backend/src/config.ts` (`PORT \|\| 3001`) |
| Site Vite (dev) | 5173 | `frontend/vite.config.ts`, `/api` → `:3001` proxy |
| Postgres (Homebrew, Docker'sız) | **5433** | `LOCAL_RUN.md` |
| Postgres (Docker Compose) | **5434** | `docker-compose.yml` |

Prod'da site kendi build'ini servis eder, tek port olur.

## Deploy

```mermaid
graph TB
    GH["GitHub<br/>bestekarx/humetric"] --> DOK["Dokploy"]
    GHS["GitHub<br/>humetric-site"] --> DOK
    DOK --> C1["compose: humetric-api<br/>api + worker + postgres"]
    DOK --> C2["application: humetric-site<br/>Dockerfile, :3001"]
    C2 --> C1
    TR["Traefik"] --> C2
    TR --> C1
```

Dokploy host adresi, proje ve servis ID'leri **gitignored** `CLAUDE.local.md`
dosyasındadır — bu repoya yazılmaz.

## Devamı

- Sinyal boru hattı, promptlar, pack'ler → [`pipeline.md`](pipeline.md)
- Tablolar, RLS, hafıza katmanları → [`data-model.md`](data-model.md)
- İki MCP sunucusu → [`mcp.md`](mcp.md)
- Site iç mimarisi → [`site.md`](site.md)
