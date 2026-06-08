# Bulk Signal — Token Maliyeti Minimizasyon Planı

> **Amaç:** Tek HTTP isteğiyle gelen N adet signal'i, mevcut "her signal = 2 LLM + 1 embedding" modelinden çok daha ucuz işlemek.
> **Müşteri korkusu:** Token maliyeti. Bu plan onu agresif şekilde düşürür.

---

## 1. Mevcut Maliyet Anatomisi (Nereye para gidiyor?)

`worker.py::process_signal_task` her signal için:

| Adım | Model | Maliyet karakteri |
|---|---|---|
| `extract_metrics` | Haiku | Ucuz ama **sistem prompt + tool şeması her çağrıda tekrar** |
| `curate_metrics` | **Sonnet** | **Pahalı. Her signal'de çalışıyor.** Asıl maliyet kalemi |
| `update_entity_embedding` | Voyage | Token başına ucuz, ama **tek tek çağrı** (round-trip israfı) |

**Sorunlu noktalar (`base.py::structured_call`):**
- Sistem prompt'u (~400 tok) + tool JSON şeması (~300 tok) **her çağrıda** input'a ekleniyor.
- Prompt cache var (`cache_control: ephemeral`) ama 5 dk TTL → seyrek/dağınık signal'de cache nadiren tutuyor.
- Senkron `messages.create` kullanılıyor → **Batches API'nin %50 indiriminden yararlanılmıyor.**

---

## 2. Yedi Kaldıraç (Etki sırasına göre)

| # | Kaldıraç | Tahmini tasarruf | Zorluk |
|---|---|---|---|
| **L1** | **Anthropic Message Batches API** — async, **%50 indirim** | %50 (her şeyde) | Düşük |
| **L2** | **Entity bazında signal birleştirme** — N ham signal → distinct entity sayısı kadar çağrı | %40–80 (senaryoya bağlı) | Düşük |
| **L3** | **Curator'ı (Sonnet) koşullu çalıştır** — sadece çakışmada | %60–85 (Sonnet kaleminde) | Orta |
| **L4** | **Prompt cache'i batch içinde paylaştır** — sistem+tool 1 kez | input prompt'un ~%90'ı | Düşük |
| **L5** | **Single-pass** — extract+merge tek Haiku çağrısı | %50 çağrı sayısı | Orta |
| **L6** | **Embedding'i batch'le** — `embed([t1..t128])` | round-trip + rate-limit | Düşük |
| **L7** | **Deterministik metrikleri LLM'den tamamen çıkar** — `/v1/metrics/bulk` | %100 (o metriklerde) | Düşük |

> **L7 hatırlatma:** Hareket hızı, iade oranı, tedarik güvenilirliği gibi metrikler matematik. Onlar **bu endpoint'e hiç girmez** → ayrı `POST /v1/metrics/bulk` (LLM'siz). Bulk **signal** sadece serbest metin gerektiren metrikler (örn. `kalite_skoru`) içindir.

---

## 3. Yeni Endpoint: `POST /v1/signals/bulk`

Tek istekte signal paketi alır, **202** + `batch_id` döner. N adet ayrı task yerine **gruplu task** oluşturur.

### Request

```json
{
  "signals": [
    {"entity_id": "product-123", "entity_type": "stok_karti", "text": "...", "external_id": "mov-2026-06-08-123"},
    {"entity_id": "product-123", "entity_type": "stok_karti", "text": "..."},
    {"entity_id": "product-999", "entity_type": "stok_karti", "text": "..."}
  ],
  "options": {
    "mode": "batch_api",          // "sync" | "batch_api" (varsayılan: batch_api → %50 indirim)
    "aggregate_per_entity": true, // aynı entity'nin signal'lerini birleştir
    "single_pass": true,          // extract+merge tek Haiku; Sonnet'e sadece çakışmada çık
    "curator_delta_threshold": 0.25 // bu eşiğin üstünde fark varsa Sonnet devreye girer
  }
}
```

### Response (202)

```json
{
  "batch_id": "bulk-7f3a...",
  "accepted": 3,
  "distinct_entities": 2,
  "mode": "batch_api",
  "status_url": "/v1/signals/bulk/bulk-7f3a..."
}
```

### Sınırlar
- Tek istekte max **10.000 signal** (Batches API tek batch'te 100K request kaldırır ama HTTP payload'ı koru).
- `Idempotency-Key` per-signal `external_id` ile → tekrar gönderim çift işlemez.

---

## 4. İşleme Akışı (token-tasarruf çekirdeği)

```
POST /v1/signals/bulk
   │
   ├─ 1. external_id ile idempotency dedup (zaten işlenmişi at)
   │
   ├─ 2. entity_id'ye göre GRUPLA + metinleri birleştir   ◄── L2
   │      product-123: "signal A\n\nsignal B"  (tek metin)
   │
   ├─ 3. entity_type'a göre grupla → pack prompt+tool ŞEMASI 1 kez  ◄── L4 (cache)
   │
   ├─ 4. Her distinct entity için 1 "extract+merge" request hazırla  ◄── L5
   │      (Haiku, single-pass: mevcut metrikleri de prompt'a koy,
   │       doğrudan final değer iste)
   │
   ├─ 5. Hepsini Anthropic MESSAGE BATCHES API'ye gönder  ◄── L1 (%50)
   │      requests=[{custom_id: entity_id, params: {...cache_control...}}]
   │
   ├─ 6. Sonuç geldikçe: çakışma var mı? (yeni vs mevcut delta > eşik)
   │      ├─ Hayır → doğrudan upsert (Sonnet YOK)            ◄── L3
   │      └─ Evet  → o entity'leri 2. batch'te Sonnet curator'a yolla
   │
   └─ 7. Değişen entity'lerin embed metnini TOPLA → embed([...128]) ◄── L6
```

### Anahtar tasarım kararları

**a) Single-pass extract+merge (L5)**
Yeni bir `agents/bulk_extractor.py` — Haiku'ya hem signal hem mevcut metrikleri verip **doğrudan final değer** ister. Çoğu signal (yeni gözlem veya küçük güncelleme) Sonnet'e hiç gitmez.

```python
# agents/bulk_extractor.py — single-pass: extract + ilk-merge tek Haiku çağrısı
async def extract_and_merge(signal_text, existing_metrics, pack_prompt, pack_metrics, tenant_id):
    # mevcut metrikleri prompt'a göm → model "yeni gözlem mi, güncelleme mi" karar verir
    # çıktı: final metrics + her biri için "conflict" bayrağı
    ...
```

**b) Koşullu Sonnet (L3)**
Curator yalnızca şu durumda:
- Metrik zaten var **VE** `|yeni_değer − mevcut_değer| > curator_delta_threshold`

Aksi halde `curator.py`'deki deterministik ilk-gözlem mantığı (satır 83–91) yeterli; Sonnet harcamasını atla.

**c) Batches API entegrasyonu (L1)**
`base.py`'ye `structured_call_batch(requests: list)` ekle:

```python
# base.py — yeni: toplu, %50 indirimli çağrı
async def structured_call_batch(requests, model, system, schema, tool_ad, tool_aciklama, tenant_id):
    client = _get_client()
    batch = await asyncio.to_thread(lambda: client.messages.batches.create(
        requests=[{
            "custom_id": r["custom_id"],
            "params": {
                "model": model,
                "max_tokens": config.MAX_TOKENS,
                # sistem + tool HER request'te aynı → cache_control ile 1 kez ücretlenir (L4)
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "tools": [{"name": tool_ad, "description": tool_aciklama,
                           "input_schema": schema.model_json_schema(),
                           "cache_control": {"type": "ephemeral"}}],
                "tool_choice": {"type": "tool", "name": tool_ad},
                "messages": [{"role": "user", "content": r["user"]}],
            },
        } for r in requests]
    ))
    return batch.id  # worker poll eder
```

**d) Worker: yeni task tipi `bulk_signal_process`**
`worker.py`'ye dispatch ekle. Bu task:
1. Batch'i submit eder, `batch_id`'yi task payload'a yazar, **poll** durumuna geçer.
2. Worker loop'u `messages.batches.retrieve(batch_id)` ile yoklar (5 dk TTL'e takılmadan, async).
3. `ended` olunca `batches.results(batch_id)` stream'ini okur, custom_id (=entity_id) ile eşler, upsert + embed yapar.

> Batch sonucu genelde dakikalar içinde döner (SLA 24h). Worker zaten async poll mimarisinde — uyumlu.

**e) Embedding batch (L6)**
`store.py`'ye `update_entity_embeddings_bulk(entity_ids, texts)` — tek `provider.embed(texts)` çağrısı (128'lik chunk), tek commit.

---

## 5. Token Maliyeti Karşılaştırması

**Senaryo:** Gecelik bulk. 100K üründen **5.000'i** hareket görmüş (sadece onlar signal üretir). Ürün başına ort. 2 ham signal → 10.000 ham signal, 5.000 distinct entity.

| Yaklaşım | Haiku çağrı | Sonnet çağrı | Embed çağrı | Sistem prompt maliyeti | Göreli maliyet |
|---|---:|---:|---:|---|---:|
| **Naive (mevcut /v1/signals)** | 10.000 | 10.000 | 10.000 | her çağrıda tam | **1.00× (taban)** |
| + Entity birleştirme (L2) | 5.000 | 5.000 | 5.000 | her çağrıda tam | ~0.50× |
| + Single-pass + koşullu Sonnet (L3,L5) | 5.000 | ~750 | 5.000 | her çağrıda tam | ~0.22× |
| + Prompt cache (L4) | 5.000 | ~750 | 5.000 | ~0.1× (cache read) | ~0.12× |
| + Embedding batch (L6) | 5.000 | ~750 | ~40 | ~0.1× | ~0.11× |
| **+ Batches API %50 (L1)** | 5.000 | ~750 | ~40 | ~0.1× | **~0.06×** |

> **Net sonuç: naive'in ~%6'sı → ~16× tasarruf.** Üstüne deterministik metrikler (L7) zaten LLM'e hiç girmediği için gerçek üretimde fark daha da büyük.

*(Rakamlar mertebe tahminidir; gerçek oran pack metrik sayısı, signal uzunluğu ve çakışma oranına göre değişir. `monitor.py` + `telemetry.log_call` ile ölçülmeli.)*

---

## 6. Uygulama Adımları (dosya dosya)

| # | Dosya | Değişiklik |
|---|---|---|
| 1 | `schema.py` | `BulkSignalCreate`, `BulkSignalItem`, `BulkSignalOptions`, `BulkSignalAccepted` modelleri |
| 2 | `api.py` | `POST /v1/signals/bulk` + `GET /v1/signals/bulk/{batch_id}` (durum) |
| 3 | `store.py` | `create_bulk_task`, `update_entity_embeddings_bulk`, `bulk_check_idempotency` |
| 4 | `agents/base.py` | `structured_call_batch(requests, ...)` — Batches API |
| 5 | `agents/bulk_extractor.py` | **yeni** — single-pass extract+merge (Haiku) |
| 6 | `worker.py` | `bulk_signal_process` task tipi: submit → poll → results → upsert → batch-embed |
| 7 | `config.py` | `BULK_MODE_DEFAULT`, `CURATOR_DELTA_THRESHOLD`, `BULK_MAX_SIGNALS`, `EMBED_BATCH_SIZE` |
| 8 | `monitor.py` | bulk batch token/latency metrikleri |
| 9 | `docs/` + Postman | endpoint dokümantasyonu + örnek koleksiyon |

---

## 7. Config (yeni env değişkenleri)

```bash
# Bulk signal
HUMETRIC_BULK_MODE_DEFAULT=batch_api      # batch_api | sync
HUMETRIC_BULK_MAX_SIGNALS=10000           # tek istek üst sınırı
HUMETRIC_CURATOR_DELTA_THRESHOLD=0.25     # bu fark üstünde Sonnet devreye girer
HUMETRIC_EMBED_BATCH_SIZE=128             # embedding chunk
HUMETRIC_BATCH_POLL_INTERVAL_S=15         # batch sonucu yoklama aralığı
```

---

## 8. T-One Tarafı Kullanım

`IHuMetricService`'e tek metot:

```csharp
Task<string> SendBulkSignalsAsync(
    IEnumerable<BulkSignalItem> signals,
    CancellationToken ct = default);   // batch_id döner
```

Gecelik Hangfire job, 5.000 ürünün metnini **tek pakette** yollar:

```csharp
var batch = movements
    .GroupBy(m => m.ProductId)
    .Select(g => new BulkSignalItem(
        EntityId: $"product-{g.Key}",
        EntityType: "stok_karti",
        Text: BuildSignalText(g.ToList()),
        ExternalId: $"nightly-{DateTime.UtcNow:yyyyMMdd}-{g.Key}"))
    .ToList();

var batchId = await huMetric.SendBulkSignalsAsync(batch, ct);
// 10.000 HTTP isteği yerine 1 istek; HuMetric içeride %50 indirimli batch'ler
```

---

## Özet

1. **Deterministik metrikler LLM'e hiç girmez** (L7, ayrı endpoint).
2. **Metin metrikleri** bulk endpoint'e gider → entity bazında birleştirilir (L2), tek Haiku ile işlenir (L5), Sonnet sadece gerçek çakışmada çalışır (L3), sistem prompt cache'lenir (L4), embedding'ler batch'lenir (L6).
3. **Hepsi Anthropic Batches API üzerinden** → her şeyde otomatik %50 indirim (L1).
4. Net etki: gecelik 100K stok senaryosunda **naive maliyetin ~%6'sı.**
