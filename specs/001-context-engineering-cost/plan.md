# Implementation Plan: Bağlam Mühendisliği ve Maliyet Optimizasyonu

**Branch**: `001-context-engineering-cost` | **Date**: 2026-09-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-context-engineering-cost/spec.md`

## Summary

HuMetric'in LLM çağrısına **ne girdiği** ve **ne kadara girdiği** bugün hiçbir yerde
ölçülmüyor; prompt cache'i doğru yere konmuş ama cache'lenen prefix yapılandırılmış modelin
minimum eşiğinin altında kaldığı için **sessizce hiç devreye girmiyor**; ve bu görünmüyor,
çünkü kullanım kaydı yalnızca tek bir toplam token sayısı tutuyor.

Yaklaşım, iki sorunu tek düzeltmeyle çözmek üzerine kurulu: **değişmez çıkarım kurallarını,
rubric'i ve çıktı-biçimi çapalarını cache'lenen sistem bloğuna taşımak** hem pack override'ında
kaybolan kalite kurallarını geri getirir hem prefix'i cache eşiğinin üstüne çıkarır. Bunun
öncesinde ve sonrasında ölçüm gelir: önce token muhasebesi ayrıştırılır (davranış değişikliği
sıfır, tek başına dağıtılabilir), sonra cache'in gerçekten okunduğu kalıcı bir kontrolle
kanıtlanır.

Aynı hata `../humetric-site` MCP ajanlarında bağımsız olarak tekrarlanmış ve sistemdeki en
büyük tek maliyet kalemi orada: sihirbazın serbest metin girdilerinde üst sınır yok ve bu
içerik ajanın her turunda, caching olmadan yeniden gönderiliyor. İki repo için tek bir bağlam
sözleşmesi yazılmasının nedeni budur.

**Uygulama sırası (kullanıcı kararı):** A1 → C1 → (A3 + B1) → A2 → B2 → B3 → C2 → C3.

> **2026-09-08 güncellemesi — yerel doğrulama DeepSeek ile koşuyor.** Yerel kiracı 1 zaten
> `llm_provider = deepseek`. Canlı ölçüm, bu sağlayıcıda cache'in **otomatik** olduğunu ve
> ~760 token'lık bir prefix'te bile çalıştığını gösterdi (`spec.md` § Clarifications, ikinci
> tur). Dolayısıyla yukarıdaki "cache sessizce hiç devreye girmiyor" teşhisi **işaretleme
> gerektiren sağlayıcı yoluna özgüdür**; A3'ün gerekçesi her iki yolda da kalite olarak
> kalır, B1'in kanıtı ise sağlayıcıya göre farklı şey ispatlar (FR-021, SC-003).

## Technical Context

**Language/Version**: Python 3.11+ (motor); TypeScript / Node (site — ayrı depo)

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 (async), Pydantic v2, Alembic, çok
sağlayıcılı LLM SDK'ları (BYOK: Anthropic / OpenAI / Google / DeepSeek)

**Storage**: PostgreSQL 16 + pgvector; kiracı izolasyonu Row-Level Security ile

**Testing**: pytest (canlı Postgres+pgvector gerektirir; `tests/` gitignore'da, yerel);
site tarafında kendi test komutları

**Target Platform**: Linux sunucu (API + worker); yerel geliştirme macOS/Homebrew

**Project Type**: Backend HTTP API + arka plan worker (motor). Site ayrı bir projedir.

**Performance Goals**: Bu özellik gecikme hedefi getirmiyor; hedef **maliyet** —
sinyal başına token maliyetinde ölçülebilir düşüş ve cache okuma oranının sıfırdan
çıkması. Zaman aşımı eklenmesi, tek asılı çağrının sıralı worker kuyruğunu kilitlemesini
önleyerek throughput'u dolaylı iyileştirir.

**Constraints**:
- A1 fazı **üretilen metrik değerlerini değiştirmemelidir** (aynı girdi → aynı metrik
  değerleri). İz kaydına yeni bir ölçüm alanı eklenmesi bu kısıtın ihlali değildir.
- `SignalCreate.text`'in 300.000 karakter sınırı bu turda değişmez (kırıcı olur).
- `/v1/query` yanıt şekli varsayılan davranışta değişmez (geriye dönük uyumluluk).
- Bütçe aşımında metin **kırpılamaz**.
- Sağlayıcı raporlamıyorsa token alanı `NULL` kalır; sıfır ya da tahmin yazılamaz.
- Runtime'da sağlayıcının sayım ucuna sinyal başına ağ çağrısı eklenmez.

**Scale/Scope**: Motorda ~6 modül + 1 yeni modül + 1 migration; site tarafında ~5 dosya.
11 metric pack, 44 route (yeni route eklenmiyor), 4 LLM sağlayıcısı.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

*Source: `.specify/memory/constitution.md` v1.0.0.*

| # | Gate | Status | Justification |
|---|------|--------|---------------|
| I | **Kiracı izolasyonu** | **PASS** | Yeni route yok. Yeni `tenant_id` tablosu yok — token kolonları mevcut `llm_call_record` tablosuna eklenir; tablo RLS'i `020`'de etkinleşmiş, politika gövdesi `022` ile güvenli `NULLIF(...)` biçimine yeniden yazılmış ve kolon eklemesi satır politikasını etkilemez (gerekçe: research R7, migration docstring'ine de yazılacak). Yazma yolu (`services/usage_service.py`) tek noktadır ve mevcut oturum davranışı korunur. |
| II | **Sağlayıcı bağımsızlığı (BYOK)** | **PASS** | Yeni LLM ajanı eklenmiyor; mevcut ajanlar dağıtıcı üzerinden çağrılmaya devam eder. Cache işaretlemesi ve zaman aşımı sağlayıcı dallarının **içinde** kalır — ortak bir cache soyutlaması dayatılmaz (research R6). Marka model adı yalnızca `config.py`'deki ortam değişkeni varsayılanlarında kalır; bu belge ve spec "yapılandırılmış sağlayıcı" der. Yeni ayarların tamamı `config.py`'den okunur. |
| III | **Rıza kapısı** | **PASS** | Yeni kişisel veri alanı yok. Bütçe aşımı işaretlemesi mevcut inceleme kalıbını kullanır ve iz kaydına **yalnızca ölçülen token sayısı** yazılır — sinyal metni ya da metrik değeri taşımaz. Rıza kapısı (`worker.py:196-204`) değişmeden kalır ve bütçe işaretlemesinden önce çalışır: rıza reddedilen metrik hiç yazılmaz, dolayısıyla işaretlenecek satır da oluşmaz. |
| IV | **Sır hijyeni** | **PASS** | Sağlayıcı uç noktası, anahtar, sunucu adresi veya kişisel yol izlenen hiçbir dosyaya girmiyor. Yeni ayarlar ortam değişkeni varsayılanı olarak tanımlanır. Doğrulama adımlarında yerel adresler `localhost` biçiminde yazılır. **Not:** `specs/` dizini gitignore'da değildir — bu plan ve türevleri izlenen dosyalardır ve bu kurala tabidir. |
| V | **Şema evrimi** | **PASS** | Tek migration, yalnızca nullable kolon ekliyor → `HM-DATA-03` sınıfı **GÜVENLİ**. `downgrade()` tam olarak kendi eklediği kolonları düşürür; referans şekil `018_usage_record_client_dims.py`. `drop`, `DELETE`, `TRUNCATE`, yeni `NOT NULL` ve eşzamanlı olmayan index yok. CI'ın upgrade → downgrade → upgrade turu doğrulama listesinde. |
| VI | **Depo sınırı** | **PASS** | Bu depoya hiçbir frontend/site dosyası eklenmiyor. C1/C2/C3 fazları tamamen `../humetric-site` projesinde uygulanır ve o depoda commit edilir. Promptlar `prompts/*.md` içinde dışsallaştırılmış kalır — R3'ün eklediği değişmez blok da oraya yazılır, Python'a gömülmez. Motorun pack YAML'ını **doğrulama** rolü ve sihirbazın site'a ait olması korunur (C4). Tüm yeni ayarlar `config.py` üzerinden okunur. |

**Re-check after Phase 1**: Faz 1 tasarımından sonra altı kapı yeniden değerlendirildi;
**hiçbirinin durumu değişmedi, altısı da PASS.** Faz 1'de netleşen ve kapıları ilgilendiren üç
nokta:

- **(I)** Veri modeli, token kolonlarını mevcut tabloya eklemeyi ve `token_count` toplam
  kolonunun anlamını **değiştirmemeyi** kesinleştirdi; yeni tablo ihtiyacı doğmadı, dolayısıyla
  yeni RLS politikası da gerekmedi.
- **(III)** İz kaydı sözleşmesi (`contracts/trace-budget-flag.md`) aşım notunun taşıyabileceği
  alanları **kapalı bir listeye** bağladı; metin ya da metrik değeri taşınamaz.
- **(VI)** Site sözleşmeleri ayrı dosyalara (`contracts/site-*.md`) çıkarıldı ve her birine
  "bu depoda uygulanmaz" notu düşüldü.

Complexity Tracking tablosu **boş kalır** — gerekçelendirilmesi gereken ihlal yok.

## Project Structure

### Documentation (this feature)

```text
specs/001-context-engineering-cost/
├── plan.md              # Bu dosya (/speckit-plan çıktısı)
├── spec.md              # Özellik spesifikasyonu
├── research.md          # Faz 0 çıktısı — R1..R14
├── data-model.md        # Faz 1 çıktısı
├── quickstart.md        # Faz 1 çıktısı — doğrulama rehberi
├── contracts/           # Faz 1 çıktısı
│   ├── llm-call-record.md      # Token muhasebesi sözleşmesi
│   ├── context-measurement.md  # Ölçüm modülünün arayüzü
│   ├── prompt-composition.md   # Değişmez blok + pack çerçevesi
│   ├── trace-budget-flag.md    # Bütçe aşımı işaretleme sözleşmesi
│   ├── query-rerank-flag.md    # /v1/query yeniden sıralama anahtarı
│   └── site-wizard-input.md    # Site: sihirbaz girdi sınırı + caching
└── tasks.md             # Faz 2 (/speckit-tasks üretir — bu komut üretmez)
```

### Source Code (repository root)

```text
src/humetric/
├── context.py                  # YENİ — bağlam ölçümü (A1)
├── config.py                   # bütçe, zaman aşımı, tahmin katsayısı, tavan ayarları
├── schema.py                   # PackPrompts alanları + extra="forbid"; QueryRequest anahtarı
├── worker.py                   # bütçe aşımı işaretlemesi (mevcut iz kalıbına eklenir)
├── batch_worker.py             # batch kimliği kalıcılığı
├── api.py                      # /v1/query koşullu yeniden sıralama
├── agents/
│   ├── base.py                 # cache/token alanlarının okunması, zaman aşımı, batch poll sınırı
│   ├── multi_llm.py            # sağlayıcı başına token alanları + zaman aşımı
│   └── extractor.py            # prompt kompozisyonu: ezme → birleştirme
├── services/
│   └── usage_service.py        # token alanlarının yazılması; harcama tavanı girdisi
├── middleware/
│   └── billing_guard.py        # token/harcama tavanı
└── db/
    └── models.py               # LlmCallRecord yeni kolonlar

alembic/versions/
└── 023_llm_call_token_breakdown.py   # YENİ — nullable kolon ekleme

prompts/
└── extractor-default.md        # değişmez blok buraya taşınır/büyütülür

scripts/
├── calibrate_token_estimate.py # YENİ — tahmincinin sapmasını raporlar (çevrimdışı)
├── verify_cache_prefix.py      # YENİ — CI'da anahtarsız prefix stabilite + eşik kontrolü
└── cost_bench.py               # MEVCUT — önce/sonra maliyet karşılaştırması

docs/architecture/
├── overview.md                 # 6. dosyaya link eklenir
└── context-engineering.md      # YENİ — dört sözleşme + Mermaid diyagramı

tests/                          # gitignore'da; yerel
```

**Ayrı depo — `../humetric-site` (bu depoya dosya eklenmez):**

```text
backend/src/
├── mcpServer/tools.ts          # C1 — sihirbaz girdi sınırları
├── agentic/
│   ├── loop.ts                 # C2 — breakpoint yerleşimi
│   └── tokenUsage.ts           # C3 — cache token'ının ayrıştırılması
├── agent/anthropicClient.ts    # C3 — zaman aşımı
├── promptLoader.ts             # C2 — kiracı belleğinin prefix'ten çıkarılması
├── agentic/signalGraph/nodes.ts# C2 — çağrı noktası
└── prompts/                    # C4 — wizard/critique promptlarının dosyaya çıkarılması
backend/schema.sql              # C3 — llm_token_usage cache kolonları
```

**Structure Decision**: Motorun mevcut tek-dosya-per-sorumluluk düzeni korunuyor. Tek yeni
modül `src/humetric/context.py`; tek sorumluluğu extraction bağlamını ölçmek. Yeni route
eklenmediği için `api.py`'nin kabul edilmiş satır borcu (`STANDARDS.md` §10.1) büyümüyor —
`/v1/query` değişikliği mevcut handler içinde birkaç satır. Site değişiklikleri o deponun
mevcut dizin yapısına oturuyor; buraya hiçbir dosya eklenmiyor (İlke VI).

---

## Faz planı

Sıra kullanıcı tarafından kararlaştırıldı ve gerekçesi şu: ölçüm olmadan sonraki hiçbir fazın
işe yaradığı **kanıtlanamaz**; A1 davranış değiştirmediği için tek başına güvenle dağıtılabilir.
Hemen ardından C1 gelir — en yüksek getirili ve en küçük değişiklik. A3 ile B1 birlikte yürür:
A3 cache'lenen bloğu büyütür, B1 bunun gerçekten cache'lendiğini kanıtlar. Sözleşme belgesi
(Çıktı 1) A1 ile birlikte yazılır ve sonraki fazlarda güncellenir.

### Çıktı 0 — `docs/architecture/overview.md` § İş katmanları — büyük resim

Bu planın kapsamı sistemin tek bir hücresidir (Katman 2 ↔ Katman 3 sınırı — extraction
çağrısının bağlamı ve cache'i). Okuyucunun o hücreyi bütünün neresinde durduğunu görebilmesi
için `overview.md`'ye, mevcut topoloji diyagramının yanına, iş sorumluluğuna göre katmanlanmış
bir genel bakış (altı katman: istemciler → site iş katmanı → motor API → motor iş mantığı →
veri → dış sağlayıcı) ve üç ana iş akışının (sinyal işleme, ajan oturumu, sorgu) diyagramları
eklendi. Yeni bir mimari değil — `overview.md`, `pipeline.md`, `site.md`'deki mevcut anlatının
tek sayfada birleştirilmiş hâli. Bu planın kendi çıktısı olan Çıktı 1 (aşağıda), o genel
resmin yalnızca bağlam/cache dilimini derinleştirir.

### Çıktı 1 — `docs/architecture/context-engineering.md` (A1 ile birlikte)

Klasör repo-içi referanstır ve VitePress'ten `srcExclude` ile hariçtir; Türkçe yazım
`HM-CONV-03` istisnasında zaten tanımlı. Mevcut beş dosyanın (`overview`, `pipeline`,
`data-model`, `mcp`, `site`) yanına altıncı olarak girer ve `overview.md`'den linklenir.

Dört sözleşme:
1. **Bağlam bütçesi** — bir extraction çağrısına ne girer, hangi sırayla, hangi üst sınırla;
   bütçe aşılırsa ne olur (sessiz kırpma **değil**).
2. **Prompt kompozisyonu** — değişmez taban blok + pack'in alan çerçevesi; bugünkü "pack
   promptu tabanı tamamen ezer" davranışının neden bir hata olduğu.
3. **Cache katmanları** — neyin stabil neyin uçucu olduğu, prefix'in neden minimumun üstünde
   tutulmak zorunda olduğu, sağlayıcı farklarının nerede soyutlandığı.
4. **Maliyet muhasebesi** — hangi token türü nerede kaydedilir, tavan nerede uygulanır.

Mermaid diyagramı: sinyal → bağlam montajı → bütçe kapısı → cache'li prefix / uçucu kuyruk →
sağlayıcı ayrımı.

### A1 — Önce ölçüm (davranış değişikliği yok) · **P1**

Bu faz hiçbir metrik değerini değiştirmez; yalnızca körlüğü kaldırır (iz kaydına ölçüm alanı
eklenir).

- **Yeni `src/humetric/context.py`** — `estimate_tokens(text) -> int` ve
  `measure_extract_inputs(system, user)`. Yerel, deterministik tahmin; sağlayıcının sayım ucuna
  runtime çağrısı yok (research R5). Sözleşme: `contracts/context-measurement.md`.
- **`agents/base.py:167`** — kullanım nesnesinden girdi, çıktı ve **cache** token alanlarını
  ayrı ayrı oku. `multi_llm.py:296` ve `:420` (OpenAI/Google yolları) için sağlayıcı
  karşılığını **implementasyondan önce doğrula**; alan yoksa `None` bırak (research R6).
- **`services/usage_service.py:65-87`** — `_insert_llm_call_record` tek yazma noktasıdır; yeni
  alanlar buradan geçer. `record_llm_tokens`'ın imzası genişler.
- **`alembic/versions/023_llm_call_token_breakdown.py`** — nullable kolonlar. Yeni RLS
  gerekmez; **gerekçe migration docstring'ine yazılır** ki bir sonraki okuyucu atlandığını
  sanmasın. Politika biçimi referansı `022`'dir — `020`'nin diskteki hâli çıplak biçimi taşıdığı
  için şablon olarak kopyalanamaz.
- **`scripts/calibrate_token_estimate.py`** — tahmincinin sapmasını gerçek sayım ucuna karşı
  çevrimdışı ölçer; katsayı `config.py` varsayılanına yazılır.

**Kabul kriteri:** Bir sinyal işlendikten sonra o çağrının kaç input token'ı olduğu, kaçının
cache'ten geldiği veritabanından sorgulanabiliyor **ve** üretilen metrik değerleri faz
öncesiyle birebir aynı.

### C1 — Sihirbazın girdi sınırı (site) · **P1**

`humetric_pack_wizard_start`'ın `text`, `db_schema` ve `sample_data` parametrelerine üst sınır.
Doğru kalıp aynı dosyada zaten var (signal chat'in sınırlı `text` alanı); sihirbaza
uygulanmamış. Sınır **doğrulama katmanında** uygulanır — hiçbir LLM çağrısı yapılmadan ve
hiçbir kredi düşülmeden. Sözleşme: `contracts/site-wizard-input.md`.

**Kabul kriteri:** Sınır üstü girdi reddedilir; o istekte LLM çağrısı ve kredi düşümü yoktur.

### A3 + B1 — Prompt kompozisyonu ve cache kanıtı (birlikte) · **P2**

**A3 — ezme yerine birleştirme:**
- `agents/extractor.py:26`'daki `pack_prompt or _DEFAULT_SYSTEM` kalıbı kaldırılır. Değişmez
  blok her zaman render edilir; pack yalnızca alan çerçevesi ekler.
- Keşifte ortaya çıkan ek düzeltme: ölçek, `source_span` ve belirsizlik kuralları bugün
  **user** mesajının içinde (`extractor.py:64-72`) — sistem bloğuna taşınır. Bu tek hamle hem
  kural tekrarını bitirir hem prefix'i cache eşiğine yaklaştırır.
- `schema.py:468-471` — `PackPrompts`'a `rubric` / `examples` alanları eklenir ve **yalnızca bu
  alt modele** `extra="forbid"` konur. `PackDefinition`'ın tamamına koymak mevcut pack'leri kırar.
- Çıktı kesilmesi tespiti: durma nedeni token sınırıysa açık hata (research R4).
- `HM-REAS-05`: bu faz metrik değerlerini değiştirebilir. `prompt_hash` değişeceği için mevcut
  replay karşılaştırması sürüklenmeyi "hash değişti, kasıtlı" olarak sınıflar
  (`replay.py:438-450`) — mekanizma var, kullanılacak. Sözleşme belgesine sürüm notu düşülür.

**B1 — cache'i çalıştır ve kanıtla:**
- A3 prefix'i büyüttükten sonra, A1'in ölçümüyle prefix'in modelin eşiğini geçtiği
  **doğrulanır**. Geçmiyorsa iki seçenek: prefix'i bilinçli olarak eşiğin üstüne çıkarmak, ya da
  o model için cache işaretlemesini kapatıp durumu net bırakmak. Kör bırakmak seçenek değil.
- **Açılış kontrolü (FR-011):** prefix her açılışta ölçülür ve yapılandırılmış eşiğin altındaysa
  `WARNING` loglanır. Tek seferlik denetim yeterli değildir — model ya da prompt sonradan
  değiştiğinde durum yeniden görünür olmalıdır.
- **Doğrulama iki parçaya ayrılır** (bu depoda `tests/` gitignore'da ve CI sahte anahtarla
  koşuyor, dolayısıyla "CI'da cache_read_tokens > 0" fiziksel olarak mümkün değil):
  - **CI'da, anahtarsız:** commit edilen `scripts/verify_cache_prefix.py` prefix'in bayt-özdeş
    üretildiğini ve eşiği geçtiğini doğrular. Sağlayıcıya hiç çağrı yapmaz.
  - **Yerel, gerçek anahtarla:** aynı prefix'le ikinci çağrıda `cache_read_tokens > 0` elle
    doğrulanır (`quickstart.md` adım 3). Cache regresyonu sessizdir; asıl koruma CI'daki
    prefix kontrolüdür.
- **Prefix stabilite denetimi:** allowed-keys sıralaması ve tool şeması serileştirmesi
  bayt-özdeş mi (research R2).
- **Sağlayıcı başına cache:** bugün yalnızca Anthropic yolunda `cache_control` var. Her
  sağlayıcının semantiği farklı; implementasyondan önce **ölçerek** doğrula, varsayım yazma.
  Soyutlama `multi_llm.py`'nin sağlayıcı dallarının içinde kalır. **DeepSeek doğrulandı
  (2026-09-08):** cache otomatik, işaretleme eklenmeyecek (FR-021); o dalda yapılacak iş
  yalnızca `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`'ın ayrı kaydedilmesidir.

**Kabul kriteri:** Pack promptu tanımlı bir pack'te değişmez blok sistem promptunda bulunur;
arka arkaya iki çağrının ikincisinde cache okuma token'ı sıfırdan büyüktür.

### A2 — Bütçe kapısı · **P3**

- `config.py`'ye extraction girdi token bütçesi. Çağrıdan önce `context.py` ile ölçüm.
- **İşaretli devam:** çağrı yapılır, metrik incelemeye alınır, iz kaydına ölçülen token
  sayısıyla aşım yazılır. Sessiz kırpma yok, sert hata yok. Kalıp yeni değil —
  `worker.py:229-232, 265` içinde `needs_review` ve `source_span_verified` için zaten
  kullanılıyor. Sözleşme: `contracts/trace-budget-flag.md`.
- `SignalCreate.text`'in 300.000 karakter sınırı **değişmez**. A1'in ölçümü gerçek dağılımı
  gösterdikten sonra ayrıca değerlendirilir.
- **Chunking bu fazda YAPILMAZ.** Chunk sonuçlarını mevcut birleştirmeyle toplamak yanlış
  olurdu: güven ağırlıklı ortalama + artan kanıt sayacı, tek sinyalin parçalarını birden fazla
  bağımsız kanıt gibi sayar (research R8). Ayrı birleştirme semantiği ister; ayrı iş.

### B2 — Gereksiz LLM çağrılarını ele · **P3**

- `api.py:1637-1647` — `/v1/query` her çağrıda sıralayıcıyı çalıştırıyor; anahtar/eşikle
  koşullu hâle gelir. **Varsayılan bugünkü davranıştır** (geriye dönük uyumluluk, `HM-REAS-05`).
  Sözleşme: `contracts/query-rerank-flag.md`.
- Retry'da tüm sinyal metninin yeniden gönderilmesi A2'nin bütçe kapısıyla görünür hâle gelir.

### B3 — Batch, tavan ve zaman aşımı · **P3**

- `batch_worker.py:175-189` — batch kimliği kalıcı hâle getirilir; gönderim ile yazma arasında
  çöken süreç **ödenmiş** batch'i kaybetmez. `submit_and_await_batch`'in `while True` yoklama
  döngüsüne (`base.py:260-264`) üst sınır konur.
- Anthropic dışı sağlayıcılar batch indiriminden yararlanmıyor (`batch_worker.py:200-218`);
  sağlayıcı başına batch desteği **doğrulanır**, varsayım yazılmaz.
- Harcama tavanı: `usage_service.TIER_LIMITS` ücretli tier'larda her boyutta sınırsız ve token
  boyutu hiç yok; `llm_token_count` kaydediliyor ama hiçbir guard okumuyor. Mevcut
  `middleware/billing_guard.py` kalıbına **günlük token tavanı** eklenir: platform anahtarıyla
  çalışan kiracıda tavan aşıldığında istek `429` ile reddedilir (mevcut `tier_limit_exceeded`
  kalıbı genişletilir, yeni mekanizma icat edilmez) ve hiçbir LLM çağrısı yapılmaz. BYOK'ta
  maliyeti kiracının kendi anahtarı öder; **BYOK kiracıya tavan uygulanmaz.** Kabul kriteri
  spec'te US7 senaryo 4-5 ve SC-011'dir.
- **Hiçbir LLM çağrısında zaman aşımı yok** (`base.py:144-155`, `multi_llm.py`). Worker sıralı
  çalıştığı için tek asılı çağrı tüm kuyruğu durdurur. Açık zaman aşımı eklenir — küçük
  değişiklik, doğrudan throughput etkisi.

### C2 — Çok turlu caching (site) · **P3**

Motorun tersine, site'ın prefix'i zaten büyük ve varsayılan ajan modelinin cache eşiği
belirgin biçimde düşük — yani breakpoint koymak tek başına işe yarar.

- Statik sistem prefix'inin son bloğuna **açık** breakpoint — garanti okuma noktası.
- Artan konuşma kuyruğu için en son eklenen turun son içerik bloğuna breakpoint; geçmiş
  büyüdükçe okuma turla birlikte artar.
- **Önkoşul:** `promptLoader.ts:32-37`'deki `injectTenantMemory`, kiracı gerçeklerini sistem
  promptunun **en önüne** ekliyor (`signalGraph/nodes.ts:62`). Bu, prefix'in ilk baytlarını her
  kiracıda farklılaştırır ve breakpoint'i işlevsiz bırakır. Kiracı belleği cache'li prefix'ten
  **sonraya** taşınmalıdır (research R10).
- Kalıcı doğrulama motorla aynı: ikinci turda cache okuma token'ı > 0.

### C3 — Site ölçümü ve mutabakat · **P3**

- `agentic/tokenUsage.ts:60-64` cache token'larını girdiye katlıyor — ayrıştırılır, yoksa
  C2'nin işe yarayıp yaramadığı ölçülemez. `backend/schema.sql:179-187`'deki `llm_token_usage`
  tablosu bugün yalnızca girdi/çıktı/çağrı taşıyor; cache kolonları eklenir.
- `context.py`'nin karşılığı TypeScript tarafında kurulur; sözleşme belgesi (Çıktı 1) iki repo
  için ortak referans olur.
- **Kredi ile gerçek harcama mutabakatı yok:** kredi MCP aracı çağrısı başına düşüyor, LLM
  çağrısı başına değil; iade yolu yok; panel rotaları hiç ücretlendirmiyor. BYOK'ta gerçek token
  maliyeti kiracının kendi faturasına gittiği için bugün tolere edilebilir — ama platform
  anahtarıyla çalışan bir akış eklenirse sabit ücretle değişken maliyet arasındaki açık doğrudan
  zarara döner. Bu sınır sözleşme belgesinde **açıkça yazılır**.
- Sitede de hiçbir zaman aşımı yok ve Anthropic yolunda SDK varsayılanı dışında retry yok
  (`agent/anthropicClient.ts`); Anthropic dışı yolda retry sarmalayıcısı var.

### C4 — Kasıtlı tekrar: korunur, netleştirilir · **NOT**

Site'ın Pack Wizard'ı motorun kendi sihirbazını **hiç çağırmıyor**; tamamen ayrı
implementasyon. Bu kullanıcının kararıyla uyumludur ve **korunur**: pack üreten araç site'a
aittir, motor yalnızca YAML'ı doğrular.

Netleştirilecekler (bu planda karar verilmiyor, tespit ediliyor):
- (a) Promptlar paralel ve maddeten farklı — motorda `prompts/wizard-system.md`, sitede inline
  TypeScript string. Site prompt-yükleme kalıbını yalnızca tek dosya için benimsemiş.
  Wizard/critique promptları da dosyaya çıkarılmalı, yoksa "promptlar versiyonlanabilir" ilkesi
  sitede geçerli değildir.
- (b) Motorun kendi sihirbazının artık kim tarafından kullanıldığı; kullanılmıyorsa ölü kod
  olarak işaretlenmeli.

---

## Doğrulama

Ayrıntılı komutlar ve beklenen çıktılar: [quickstart.md](./quickstart.md).

1. **Kalite kapıları** (`CLAUDE.md` § Quality gates ile birebir): ruff · `py_compile` ·
   pytest · Alembic upgrade → downgrade → upgrade turu.
2. **Ölçümün doğruluğu:** `LOCAL_RUN.md` ile stack ayağa kaldırılır, bir sinyal ingest edilir,
   `LOCAL_DB.md` ile çağrı kaydı sorgulanır — input/output ayrımı ve cache alanları dolu mu.
3. **Cache gerçekten çalışıyor mu:** (a) CI'da `scripts/verify_cache_prefix.py` — prefix
   bayt-özdeş ve eşiğin üstünde mi (anahtar gerektirmez); (b) yerel, gerçek anahtarla aynı
   pack'le arka arkaya iki sinyal; ikincisinde cache okuma > 0. **Planın merkezî iddiasının
   testi — geçmezse B1 bitmemiştir.**
4. **Bütçe kapısı:** bütçeyi aşan sinyal; sessizce kırpılmadığı, işaretlendiği doğrulanır.
5. **Kalite regresyonu:** replay karşılaştırmasıyla A3'ün ürettiği sürüklenmenin *hash
   değişimiyle atfedilebilir* olduğu görülür. Tek canary 11 pack'ten 1'ini kapsıyor — bu turda
   genişletilmiyor, sınırın bilinmesi yeterli.
6. **Maliyet farkı:** mevcut `scripts/cost_bench.py` ile önce/sonra token/sinyal karşılaştırması.
7. **Site — sihirbaz girdi sınırı:** sınır üstü girdi reddedilir (bugün sessizce kabul ediliyor).
8. **Site — çok turlu cache:** bir oturum iki tur ilerletilir; ikinci turda cache okuma > 0 ve
   cache token'ı girdiden ayrı kaydedilmiş. Kiracı belleği taşındıktan sonra bu değerin
   *düşmediği* kontrol edilir.
9. **Uçtan uca:** tam stack (motor + site) ayağa kaldırılır; MCP üzerinden bir sihirbaz turu ve
   bir signal chat turu çalıştırılır.

---

## Teknik borç — bu turda yapılmıyor, kayda geçiyor

Kullanıcının açık talebiyle not edilenler. Bunlar bu özelliğin kapsamı **değildir** ve ilgisiz
bir commit'te bulgu olarak raporlanmamalıdır.

**Eval / regresyon güvenliği**
- `replay.py` gerçek bir canary harness ama tek golden set var (11 pack'ten 1'i) ve **CI'da
  koşmuyor** — CI yalnızca ruff + pytest + migration turu çalıştırıyor.
- Snapshot/approval testi, LLM-as-judge skoru, kalibrasyon ölçümü yok. Depoda baseline rapor
  tutulmuyor. Batch yolunda ve boş olmayan geçmişle birleştirme için eval yok.

**Orkestrasyon dayanıklılığı**
- `_persist_signal_result` (`worker.py:165-289`) **tek transaction değil** — metrik başına
  yazılıyor; ortada çökme olursa N metrik yazılı, gerisi eksik, sinyal hâlâ işleniyor
  durumunda. Kısmi yazma yapısal olarak mümkün.
- Dead-letter tablosu yok; kalıcı hata yalnızca satır durumu — alarm/yeniden kuyruklama/admin
  retry yolu yok.
- Realtime worker bayat görev geri alımını **hiç çağırmıyor** (yalnızca batch worker çağırıyor)
  — çöken worker'ın işleri sonsuza kadar işleniyor durumunda kalıyor.
- Görev seviyesinde idempotency yok: `input_hash` yazılıyor ama hiçbir yerde karşılaştırılmıyor;
  replay kanıt sayacını kalıcı olarak şişiriyor.
- Backoff üstel ama jitter'sız ve üst sınırsız.
- Rıza nedeniyle atlanan hassas metrikler sessizce düşüyor — `skipped_sensitive` toplanıyor ama
  hiç kullanılmıyor (`worker.py:184`).

**Site tarafı (bu turda dokunulmuyor)**
- LangGraph akışında checkpointer yok — her devam START'tan yeni bir çağrı ve state yeniden
  okunuyor.
- Döngü başlatma fire-and-forget; boot recovery var ama süreç ortada ölürse tur kaybı görünmez.
- Çıktı token sınırı kodda sabit, ortam değişkeniyle yönetilmiyor; streaming yalnızca bu yüzden
  kullanılıyor ve delta'lar atılıyor.
- `.env.example` hiçbir kod tarafından okunmayan ölü değişkenler listeliyor — yanıltıcı config.
- Anthropic dışı sağlayıcılarda şema **düzyazı olarak** ekleniyor ve ayrıştırma parantez
  taramasıyla yapılıyor — motorun `c802a95`'te düzelttiği hatanın site sürümü.

**Diğer**
- Curator'ın güven formülü güven ağırlıklı ortalama olduğu için **doğrulama güveni artırmıyor**:
  iki bağımsız 0.8 gözlem yine 0.8 veriyor. Ayrıca birleştirme **decay uygulanmamış** saklı
  güvenle çalışıyor (decay yalnızca okuma yolunda) — eski bir değerle yeni bir değer eşit
  ağırlıkta.
- Ranker ve wizard çağrı meta'sı göndermiyor → çağrıları versiyonsuz. Wizard telemetrisi token
  sayısını **uyduruyor** (`wizard.py:76`, karakter sayısını dörde bölüyor). A1'in ölçümü bu
  uydurmanın yerini alabilir ama bu turda kapsam dışı.
- `api.py` 2365 satır, bölme eşiği aşılmış — `STANDARDS.md` §10.1'de kabul edilmiş borç.

## Complexity Tracking

> Yalnızca Constitution Check'te gerekçelendirilmesi gereken ihlal varsa doldurulur.

**Doldurulmadı — altı kapının altısı da PASS, gerekçelendirilecek ihlal yok.**
