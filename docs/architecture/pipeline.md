# Sinyal Boru Hattı, Promptlar ve Pack'ler

> Repo içi mimari referansı — yayınlanan siteye dahil değildir. Bkz.
> [`overview.md`](overview.md).

## 1. Ingest — senkron kısım

`POST /v1/signals` LLM çağırmaz. Doğrular, kuyruğa iş bırakır, **202** döner.

```mermaid
sequenceDiagram
    autonumber
    participant C as İstemci
    participant A as api.py<br/>create_signal
    participant S as store.py
    participant DB as PostgreSQL

    C->>A: POST /v1/signals<br/>(Idempotency-Key?)
    A->>A: _require_scope("signals:write")
    A->>S: get_entity()
    alt entity yok
        A-->>C: 404 entity_not_found
    else arşivlenmiş
        A-->>C: 403 entity_archived
    end
    A->>S: check_entity_type_writable()
    Note over S: pack var ama is_active=false → 403 entity_type_locked

    alt Idempotency-Key başlığı var
        A->>S: check_idempotency()
        S-->>A: 24 saat içinde eşleşme
        A-->>C: 200 (replay, yeni iş yok)
    else external_id çakışıyor
        A->>S: find_signal_by_external_id()
        A-->>C: 409 duplicate
    end

    A->>S: get_active_pack_for_type(entity_type)
    A->>S: create_signal(pack_key, pack_version, occurred_at)
    A->>S: create_task(signal_process, queued)
    Note over A,DB: payload["pack_definition"] = pack.definition<br/>(tam snapshot)
    A->>S: audit("signal.ingested") + record_signal()
    A-->>C: 202 {signal_id, status:"received", trace_url}
```

**Neden pack snapshot'ı:** pack tanımı `task.payload`'a kopyalanır (`api.py:882`).
İş kuyrukta beklerken pack düzenlenirse uçuştaki çıkarım değişmez — sonuç, o an
geçerli olan tanımla reprodüksiyona uygun kalır. `re_embed` task'ı bilinçli olarak
**canlı** pack'i okur (`worker.py:282-296`), çünkü embedding metni güncel hassaslık
kurallarını yansıtmalıdır.

Yarış durumu: iki eşzamanlı istek aynı `Idempotency-Key` ile gelirse
`uq_signal_idempotency` üzerinde `IntegrityError` yakalanır ve 409'a çevrilir
(`api.py:856-870`) — 500 değil.

## 2. Kuyruk — tek tablo, iki tüketici

```mermaid
graph TB
    T[("task tablosu")]
    T -->|"task_types = tümü"| W["worker.py<br/>uzun ömürlü döngü"]
    T -->|"task_types = [signal_process]"| B["batch_worker.py<br/>tek seferlik drain"]
    W --> P["worker._persist_signal_result"]
    B --> P
    W --> RE["re_embed"]
    W --> LE["lakehouse_export"]
    W --> EX["export_request"]
```

| | `worker.py` | `batch_worker.py` |
|---|---|---|
| Yaşam | Sürekli, `WORKER_POLL_INTERVAL_S` aralıklı | `python -m humetric.batch_worker`, kuyruk boşalınca çıkar |
| Task tipleri | `signal_process`, `re_embed`, `lakehouse_export`, `export_request` | Sadece `signal_process` |
| LLM çağrısı | Sinyal başına senkron | Anthropic **Batches API** (%50 maliyet); OpenAI/Google/DeepSeek tenantları senkron |
| Ek mod | — | `--weekly`: kronolojik, entity başına tek iş |
| Zamanlayıcılar | Nightly export, trial expiry, export cleanup | Yok |

Her ikisi de **admin (RLS-bypass) oturumu** kullanır, çünkü `task` tablosunda RLS
FORCE'ludur ve kısıtlı rol GUC olmadan sıfır satır görür (`worker.py:593-597`).
Tenant bağlamı iş bazında `set_config('app.tenant_id', ...)` ile kurulur, `finally`
bloğunda `''`'a çekilir (`worker.py:492-495`) — bu boş GUC durumu migration 022'nin
düzelttiği tuzağın kaynağıdır, bkz. [`data-model.md`](data-model.md#rls).

### İş kapma

```sql
-- store.py:1019-1045
SELECT * FROM task
WHERE status = 'queued'
  AND (next_retry_at IS NULL OR next_retry_at <= now())
ORDER BY created_at ASC
LIMIT :batch_size
FOR UPDATE SKIP LOCKED;
```

Kapılan satırlar hemen `processing` + `started_at` alır. Süreç çökerse satır
`processing`'de kalır; `reclaim_stale_tasks` (`store.py:1112-1127`) belirli süreden
eski `processing` satırlarını `queued`'a geri alır.

`--weekly` modunda `get_next_chronological_batch` (`store.py:1047-1110`) raw SQL CTE
ile en eski zaman kovasını bulur ve `DISTINCT ON (tenant_id, entity_id)` ile entity
başına tek iş seçer — böylece aynı entity'nin sinyalleri sırayla işlenir.

### Durum makineleri

```mermaid
stateDiagram-v2
    direction LR
    [*] --> queued: create_task
    queued --> processing: FOR UPDATE SKIP LOCKED
    processing --> completed: complete_task
    processing --> queued: schedule_retry<br/>(2^retry_count sn)
    processing --> failed: fail_task_permanently<br/>(4xx / ValueError / retry bitti)
    processing --> queued: reclaim_stale_tasks<br/>(takılı süreç)
    completed --> [*]
    failed --> [*]
```

```mermaid
stateDiagram-v2
    direction LR
    [*] --> received: POST /v1/signals
    received --> completed: update_signal_status
    received --> failed: fail_task_permanently
```

**Dikkat:** `signal.status` CHECK'i `processing`'e izin verir ama worker sinyali asla
`processing`'e çekmez — `received → completed | failed` gider. Retry sırasında sinyal
`received`'a geri alınır (`worker.py:428-442`).

### Hata sınıflandırması

```mermaid
flowchart TD
    E["İstisna"] --> Q{"4xx status<br/>veya ValueError?"}
    Q -->|Evet| F["fail_task_permanently<br/>signal → failed"]
    Q -->|Hayır| R{"retry_count &lt; max_retries<br/>(varsayılan 3)"}
    R -->|Evet| S["schedule_retry<br/>next_retry_at = now + 2^retry_count sn<br/>signal → received"]
    R -->|Hayır| F
```

LLM katmanında ayrıca SDK'nın kendi retry'ı var (`LLM_MAX_RETRIES`); `BadRequestError`
(400) retry'sız olarak yeniden fırlatılır (`agents/base.py:159-161`).

## 3. İşleme — extraction → deterministik merge → yazma

```mermaid
flowchart TD
    START["process_signal_task<br/>worker.py:75"] --> ENT["get_entity<br/>yoksa ValueError → retry'sız"]
    ENT --> KEY["get_tenant_llm_config<br/>tenant BYOK anahtarı, yoksa platform"]
    KEY --> HASH["input_hash = hash(signal_text)<br/>context_hash = hash(entity.free_text)"]
    HASH --> EXT["extractor.extract_metrics<br/>→ structured_call_multi<br/><b>tek LLM çağrısı</b>"]
    EXT --> CUR["curator.finalize_merge<br/><b>LLM yok — saf Python</b>"]
    CUR --> LOOP{"her final metrik"}

    LOOP --> KVKK{"pack'te sensitive:true<br/>+ requires_consent_scope?"}
    KVKK -->|"consent yok"| SKIP["atla<br/>(skipped_sensitive)"]
    KVKK -->|"consent var / hassas değil"| SPAN{"source_span<br/>birebir metinde?<br/>+ injection karantinası"}
    SPAN -->|Hayır| FLAG["review_status =<br/>pending_review"]
    SPAN -->|Evet| NR{"needs_review?"}
    NR -->|Evet| FLAG
    NR -->|Hayır| OK["review_status = NULL"]

    FLAG --> WRITE
    OK --> WRITE
    WRITE["append_metric_history<br/>+ upsert_metric<br/><b>tek transaction</b>"] --> LOOP

    LOOP -->|"bitti"| EMB["_build_embed_text_safe<br/>→ update_entity_embedding"]
    EMB -->|"hata"| PEND["embedding_pending = true<br/>+ re_embed task'ı kuyruğa"]
    EMB --> DONE["update_signal_status<br/>completed + result"]
    PEND --> DONE
```

### Curator artık LLM değil

`agents/curator.py:finalize_merge` (54-87) tamamen deterministiktir:

- Yeni anahtar → doğrudan geçer.
- Var olan anahtar → güven ağırlıklı ortalama:
  `value = old·(1−w) + new·w`, &nbsp;`w = new_conf / (old_conf + new_conf)`.
- `_finalize_metric` (25-51): `CONFIDENCE_THRESHOLD` (0.55) altındakileri **atar**
  (`needs_review` değilse), değeri `[-1, 1]`'e kırpar, pack'teki tipe uymayan
  metriğe **−0.2 güven cezası** uygular.

Sonuç: sinyal başına **tek** LLM çağrısı, ve trace'te `curator_model` her zaman
`None`. Curator prompt dosyası **yok** ve olmamalıdır. Bu değişiklik `43845ad`'de
yapıldı; altı pack'ten curation prompt'ları `f36d3dd`'de kaldırıldı.

### İki yazma kapısı

**KVKK kapısı** (`worker.py:181-190`) — yazma anında çalışır. Pack'te
`sensitive: true` + `requires_consent_scope` olan metrik, o entity için geçerli
consent yoksa **hiç yazılmaz**. Okuma tarafında ayrıca `kvkk.py:53-111` filtresi var;
yani hassas veri hem yazılmaz hem de rıza iptal edilirse anında görünmez olur.

**`source_span` doğrulaması** (`worker.py:45-62`) — modelin alıntıladığı kanıt
parçası, boşluklar normalize edilerek sinyal metninde birebir aranır. İki şekilde
başarısız olur:
1. Alıntı metinde yok → model uydurmuş.
2. Alıntı metinde **var** ama kendisi bir talimat gibi okunuyor
   (`_INJECTION_QUARANTINE_PATTERN`: "ignore previous instructions", "incelemeye gerek
   yok", "set X to 1.0" vb.) → prompt injection şüphesi.

Her iki durumda metrik yazılır ama `review_status = "pending_review"` alır.
Span yoksa doğrulanmış sayılır — pack prompt'ları span istemek zorunda değil.

### İnsan incelemesi

```mermaid
graph LR
    W["worker<br/>review_status=pending_review"] --> L["GET /v1/metrics/pending-review<br/>scope: packs:admin"]
    L --> R["PUT /v1/metrics/{entity}/{key}/review"]
    R --> O["reviewer_override JSONB yazılır<br/>value/confidence <b>yerinde ezilir</b><br/>review_status = reviewed<br/>audit: metric.overridden"]
```

**Bilinen davranış:** reviewer override `entity_metric_history`'ye satır **eklemez**
(`store.py:1349-1384`) — geçmiş zaman serisi sadece boru hattı yazımlarını içerir.

### Bilinen tuhaflıklar

- Embedding metni **yazma öncesi** snapshot'tan kurulur:
  `_build_embed_text_safe(entity, existing_metrics, pack_def)` (`worker.py:253`)
  `existing_metrics`'i alır, yeni yazılan değerleri değil. Yani vektör bir sinyal
  geriden gelir; bir sonraki sinyal veya `re_embed` yakalar.
- Sıra dışı yazma koruması: `upsert_metric` (`store.py:159-207`) gelen
  `last_updated` mevcut değerden eskiyse **tüm payload'ı atlar**, sadece
  `source_count`'u max-merge eder. `entity_metric` "en güncel değer" tablosudur;
  geçmiş kendi satırını yine alır.
- Batch modunda aynı entity'nin birden fazla sinyali aynı partide olursa hepsi
  parti öncesi snapshot'ı görür → son yazan kazanır (`batch_worker.py:25-32`).

## 4. Promptlar

```mermaid
graph TB
    subgraph disk["prompts/ (repo kökü, paketin içinde değil)"]
        P1["extractor-default.md"]
        P2["ranker-default.md"]
        P3["wizard-system.md"]
    end
    P1 -->|"import anında"| L["agents/__init__.py:_load_prompt<br/><b>dosya yoksa sessizce ''</b>"]
    P2 --> L
    P3 --> L
    L --> EX["agents/extractor.py:10"]
    L --> RK["agents/ranker.py:15"]
    L --> WZ["agents/wizard.py:18"]

    PACK["pack.definition<br/>prompts.extraction"] -->|"varsa <b>yerine geçer</b>"| SYS["system prompt"]
    EX --> SYS
    SYS --> APPEND["+ izinli metrik anahtarları bloğu<br/>(pack.metrics'ten türetilir)"]
    APPEND --> USER["user mesajı:<br/>&lt;signal_text&gt; + injection talimatı<br/>+ [-1,1] / confidence / source_span kuralları"]
```

Üç dosya var, üçü de canlı: `extractor-default.md`, `ranker-default.md`,
`wizard-system.md`. **Curator prompt'u yok** (deterministik).

Dikkat edilmesi gereken iki nokta:
1. Loader eksik dosyada hata vermez, boş string döner (`agents/__init__.py:1-10`).
   Yeniden adlandırma sessizce prompt'u boşaltır.
2. Pack'in `prompts.extraction` alanı default'a **eklenmez, yerine geçer**
   (`extractor.py:26`). Pack prompt'u yazan kişi `source_span` ve ölçek talimatlarını
   kendisi taşımak zorundadır — `tcmb-ppk` pack'i tam bu yüzden prod'a span talimatı
   olmadan çıktı ve `77e3a58`'de düzeltildi.

### Sürümleme = içerik hash'i

Prompt'ların sürüm numarası yok; yerine sha256 parmak izleri var
(`agents/versioning.py`):

| Hash | Neyi izler | Nereye yazılır |
|---|---|---|
| `prompt_hash` | sistem prompt metni | `entity_metric`, trace |
| `schema_hash` | tool/JSON şeması | `entity_metric`, trace |
| `input_hash` | sinyal metni | `entity_metric`, `signal` |
| `context_hash` | `entity.free_text` (bağlam kayması) | `entity_metric`, `entity_metric_history` (migration 021) |

Bir metrik değeri açıklanamaz hâle geldiğinde bu dört hash "hangi prompt, hangi şema,
hangi girdi, hangi bağlam" sorusunu tek satırda cevaplar.

## 5. Pack'ler

`packs/*.yaml` **otomatik yüklenmez.** Ayrı bir loader modülü yok; bu dosyalar
script veya konsol aracılığıyla `POST /v1/packs`'e gönderilen örneklerdir
(`scripts/demo.sh`, `scripts/walkthrough.sh`). `packs/canary/` ise `replay.py`
için fixture'dır, pack değil.

Doğrulama tamamen `schema.py`'de: `PackDefinition` (510-520), `PackMetricDef`
(441-459), `PackBand`, `PackKVKK`, `PackPrompts`, `PackFieldDef`, `PackDisplay`.

### Yaşam döngüsü — draft/published durumu **yok**

```mermaid
stateDiagram-v2
    [*] --> yok
    yok --> v1: POST /v1/packs<br/>yaml.safe_load → PackDefinition<br/>→ _check_max_metrics
    v1 --> vN: PUT /v1/packs/{key}<br/>version += 1<br/><b>definition üzerine yazılır</b>
    vN --> vN: tekrar PUT
    note right of vN
        Eski tanım saklanmaz.
        is_active hiçbir endpoint
        tarafından değiştirilmez —
        deaktivasyon DB/konsol işi.
    end note
```

Hata kodları: `422 invalid_yaml` / `validation_error`, `409 pack_already_exists`,
`409 entity_type_already_active` (bir entity_type'a yalnızca bir aktif pack).

`MAX_METRICS_PER_PACK` (varsayılan **7**) yalnızca persist sınırında uygulanır
(`api.py:1174-1189`), `PackDefinition`'da değil — wizard'ın fazla öneri yapıp
kullanıcının budaması için bilinçli bir tercih (`schema.py:522-526`).

`POST /v1/packs/wizard` YAML üretir ama **kaydetmez**; `__ranker__` / `__wizard__`
sentinel pack anahtarları pack'e ait olmayan LLM harcamasının `/v1/usage/packs`'te
mutabık kalması içindir ve `PackCreate` `__` önekini reddeder.

### Pack bir çıkarımı nasıl sürükler

```mermaid
graph LR
    PD["pack.definition"] --> RF["required_fields<br/>→ entity oluşturma kapısı<br/>422 missing_required_fields"]
    PD --> PR["prompts.extraction<br/>→ sistem prompt'u"]
    PD --> MK["metrics[].key<br/>→ izinli anahtar listesi"]
    PD --> MT["metrics[].type<br/>→ curator tip cezası"]
    PD --> SE["metrics[].sensitive +<br/>requires_consent_scope<br/>→ yazma kapısı"]
    PD --> VT["metrics[].visible_to<br/>→ okuma kapısı (kvkk.py)"]
    PD --> KV["kvkk.sensitive_metrics<br/>→ embedding'den dışlama"]
    PD --> BD["bands / direction / unit<br/>→ sunum"]
```

### Mevcut pack'ler

`bayi-ziyaret`, `cagri-merkezi`, `demo-worker`, `demo-worker-full`,
`otel-bolge-muduru`, `otel-tesis`, `saha-hizmet-cari`, `saha-hizmet-isci`,
`tcmb-ppk`, `toptanci-tedarik`, `youtube-yorum` (11) + `canary/` fixture.

Son değişiklikler: `f36d3dd` altı pack'ten curation prompt'unu kaldırdı,
`77e3a58` TCMB/PPK + çağrı merkezi pack'lerini ekledi (span + kvkk düzeltmesiyle),
`d279803` bayi ziyaret + toptancı, `fdfeea5` lastik demo domain'ini otelcilikle
değiştirdi.
