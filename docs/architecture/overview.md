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

## İş katmanları — büyük resim

> Aynı içeriğin bağımsız, kendi kendine yeten bir sayfası:
> [`business-blueprint.html`](business-blueprint.html) (Mermaid.js CDN'den yüklenir, tarayıcıda
> doğrudan açılabilir). Kanonik kaynak yine burasıdır — ikisi çelişirse buna güven.

Yukarıdaki topoloji hangi sürecin hangi porta bağlandığını gösterir; aşağıdaki diyagram aynı
sistemi **iş sorumluluğu** eksenine göre katmanlar (istemci → site iş katmanı → motor API →
motor iş mantığı → veri → dış sağlayıcı) hâlinde tekrar çizer.

```mermaid
graph TB
    subgraph L0["Katman 0 — İstemciler"]
        BR["Tarayıcı — React SPA"]
        MCPC["MCP istemcisi"]
        APIC["Doğrudan API — SDK / n8n / curl"]
    end

    subgraph L1["Katman 1 — Site iş katmanı — humetric-site, ayrı repo"]
        EXP["Express :3001 — oturum, proxy, paylaşım"]
        WIZ["Pack Wizard ajanı — Site MCP"]
        CHAT["Signal Chat ajanı — Site MCP"]
        BILL["billing.ts — kredi düşümü"]
    end
    SPG[("Site Postgres — kullanıcı, BYOK anahtar, kredi")]

    subgraph L2["Katman 2 — Motor API katmanı — humetric"]
        FAPI["FastAPI :8002 — doğrulama, kuyruğa yaz"]
        EMCP["Motor MCP — stdio, saf HTTP istemcisi"]
    end

    subgraph L3["Katman 3 — Motor iş mantığı — arka plan"]
        WRK["worker.py"]
        BWRK["batch_worker.py"]
        EXT["extractor — tek LLM çağrısı"]
        CUR["curator — deterministik birleştirme"]
        RANK["ranker — opsiyonel"]
    end
    PG[("Motor Postgres + pgvector — RLS'li 14 tablo")]

    subgraph L5["Dış sağlayıcılar"]
        LLM["LLM — BYOK"]
        EMB["Embedding sağlayıcı"]
        STR["Stripe"]
        SMTP["SMTP"]
    end

    BR --> EXP
    MCPC --> WIZ
    MCPC --> CHAT
    MCPC --> EMCP
    APIC --> FAPI

    EXP --> FAPI
    WIZ --> BILL --> SPG
    CHAT --> BILL
    WIZ --> FAPI
    CHAT --> FAPI
    EMCP --> FAPI
    EXP --> SPG
    WIZ --> LLM
    CHAT --> LLM
    EXP --> STR
    EXP --> SMTP

    FAPI --> PG
    FAPI -. kuyruk .-> WRK
    WRK --> BWRK
    WRK --> EXT --> LLM
    EXT --> CUR --> PG
    FAPI -. opsiyonel .-> RANK --> LLM
    WRK --> EMB
    WRK --> PG
```

### Üç ana iş akışı

Aynı sistemde üç işlem üç farklı yoldan geçer: sinyal işleme kuyruklu ve asenkron, ajan
oturumu çok turlu ve ücretli, sorgu senkron ve LLM'i opsiyonel.

```mermaid
flowchart TD
    C1["İstemci"] -->|"POST /v1/signals"| A1["api.py<br/>doğrula + kuyruğa al · 202"]
    A1 --> Q1[("task tablosu")]
    Q1 --> W1["worker.py"]
    W1 --> E1["extractor.py<br/>tek LLM çağrısı"]
    E1 --> CU1["curator.py<br/>deterministik birleştirme"]
    CU1 --> K1{"KVKK rızası<br/>var mı?"}
    K1 -->|hayır| SK1["atla — hiç yazılmaz"]
    K1 -->|evet| SP1{"source_span<br/>doğrulandı mı?"}
    SP1 -->|hayır| PR1["review_status =<br/>pending_review"]
    SP1 -->|evet| OK1["doğrudan yaz"]
    PR1 --> DB1[("entity_metric<br/>+ history")]
    OK1 --> DB1
    DB1 --> EM1["embedding güncelle"]
```

```mermaid
flowchart TD
    C2["Tarayıcı / MCP istemcisi"] -->|"start"| S2["Site MCP<br/>Wizard veya Signal Chat"]
    S2 --> B2["kredi düşümü<br/>billing.ts"]
    B2 --> L2["ajan turu<br/>LLM çağrısı"]
    L2 -->|"entity / signal / metric<br/>oku-yaz"| API2["Motor /v1/*"]
    API2 --> PG2[("Motor Postgres")]
    L2 --> SSE2["agent_events<br/>→ SSE akışı"]
    SSE2 --> C2
```

```mermaid
flowchart TD
    C3["Tarayıcı"] -->|"GET /api/..."| PX3["proxy.ts<br/>kimlik kendi kendini onarır"]
    PX3 -->|"Bearer hm_live_"| API3["Motor /v1/query"]
    API3 --> H3["hibrit arama<br/>vektör + tam metin"]
    H3 --> R3{"rerank = true?"}
    R3 -->|evet| RK3["ranker.py — LLM"]
    R3 -->|hayır| SK3["hibrit skorla dön"]
    RK3 --> RESP3["QueryResponse"]
    SK3 --> RESP3
    RESP3 --> PX3 --> C3
```

`001-context-engineering-cost` planı bu resmin tam olarak **Katman 2 ↔ Katman 3** sınırındaki
tek bir hücreye — extraction çağrısının bağlamına ve cache'ine — odaklanır; geri kalan her şey
o planın kapsamında değişmeden kalır.

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
- Bağlam bütçesi, prompt kompozisyonu, cache ve maliyet muhasebesi →
  [`context-engineering.md`](context-engineering.md)
