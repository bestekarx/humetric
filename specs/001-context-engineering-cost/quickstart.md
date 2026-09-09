# Quickstart — Doğrulama Rehberi

**Feature**: `001-context-engineering-cost`

Bu rehber kod yazıldıktan **sonra** çalıştırılır. Adımlar `plan.md § Doğrulama` ile birebir
aynıdır; burada gerçek komutlar var. `HM` = `psql -h localhost -p 5433 -U humetric -d humetric`
(bkz. `LOCAL_DB.md`).

## Ön koşul: stack ayakta

```bash
# LOCAL_RUN.md'ye göre — Postgres 5433, API 8002
.venv/bin/uvicorn humetric.api:app --port 8002 --host localhost &
python -m humetric.worker &
```

---

## 1. Kalite kapıları (CI parity)

```bash
.venv/bin/ruff check src/
python -m py_compile src/humetric/context.py src/humetric/agents/base.py \
  src/humetric/agents/multi_llm.py src/humetric/agents/extractor.py \
  src/humetric/services/usage_service.py src/humetric/worker.py \
  src/humetric/api.py src/humetric/schema.py src/humetric/config.py \
  src/humetric/db/models.py alembic/versions/023_llm_call_token_breakdown.py
.venv/bin/pytest -x -q --tb=short --timeout=30
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
```

**Beklenen**: hepsi yeşil. Migration turu hatasız tamamlanır.

---

## 2. Ölçümün doğruluğu (A1)

```bash
curl -s -X POST http://localhost:8002/v1/signals \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"entity_id":"e1","entity_type":"dealer","text":"Musteri cok memnun kaldi, hizli teslimat."}'

sleep 3

psql -h localhost -p 5433 -U humetric -d humetric -c \
  "select signal_id, pack_key, provider, model, token_count, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens from llm_call_record order by created_at desc limit 1;"
```

**Beklenen**: `input_tokens`, `output_tokens` dolu; `cache_read_tokens` ilk çağrıda `0` veya
`NULL` olabilir (henüz yazılmış bir cache yok), `cache_write_tokens` dolu.

**Davranış değişikliği yok kontrolü**: Bu sinyali A1 öncesi ve sonrası aynı metinle işleyip
`entity_metric.value` / `.confidence` değerlerinin birebir aynı kaldığını doğrula.

---

## 3. Cache gerçekten çalışıyor mu (B1 — planın merkezî testi)

Doğrulama iki parçadır. `tests/` gitignore'da ve CI sahte anahtarla koştuğu için (`ci.yml`,
`ANTHROPIC_API_KEY: sk-test`) `cache_read_tokens > 0` bir CI kapısı olamaz.

### 3a. CI'da koşan, anahtar gerektirmeyen kontrol

```bash
python scripts/verify_cache_prefix.py
```

**Beklenen**: prefix iki kez kurulduğunda bayt-özdeş **ve** yapılandırılmış minimum cache
eşiğinin üstünde. Sağlayıcıya hiç çağrı yapılmaz. Cache regresyonuna karşı kalıcı koruma budur.

### 3b. Gerçek anahtarla yerel manuel doğrulama (DeepSeek)

Yerel kiracı 1 `llm_provider = deepseek`; anahtar `.env`'deki `DEEPSEEK_API_KEY`. Bu
sağlayıcıda cache **otomatiktir** — yani bu adım "cache açıldı mı"yı değil, **ölçümün doğru
kaydedildiğini** doğrular (bkz. `spec.md` SC-003). İşaretleme gerektiren sağlayıcının kanıtı
ayrı bir koşudur ve kendi anahtarını ister.

```bash
# Aynı pack ile İKİNCİ bir sinyal gönder (aynı entity_type, farklı metin)
curl -s -X POST http://localhost:8002/v1/signals \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"entity_id":"e1","entity_type":"dealer","text":"Ikinci sinyal, farkli icerik."}'

sleep 3

psql -h localhost -p 5433 -U humetric -d humetric -c \
  "select signal_id, cache_read_tokens, cache_write_tokens, created_at from llm_call_record order by created_at desc limit 2;"
```

**Beklenen**: İkinci satırda `cache_read_tokens > 0` ve DeepSeek yolunda
`cache_write_tokens` `NULL` (sağlayıcı o sayıyı raporlamıyor — FR-002'nin koruduğu durum,
hata değil). **Bu sıfırsa B1 bitmemiştir** — prefix
hâlâ eşiğin altında ya da bayt-özdeş değil; `contracts/prompt-composition.md § Prefix
stabilite denetimi`'ne geri dön. Gözlenen sayılar
`docs/architecture/context-engineering.md`'ye kaydedilir (T029).

Ayrıca açılış log'unda prefix eşiğin altındaysa bir `WARNING` satırı bulunmalıdır (FR-011);
eşiğin üstündeyse böyle bir satır yoktur.

---

## 4. Bütçe kapısı (A2)

```bash
# EXTRACT_INPUT_TOKEN_BUDGET'ı düşük bir değere çekip test et
export HUMETRIC_EXTRACT_INPUT_TOKEN_BUDGET=50
# ... uvicorn/worker'ı bu env ile yeniden başlat ...

curl -s -X POST http://localhost:8002/v1/signals \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"entity_id":"e1","entity_type":"dealer","text":"Bu metin bilerek uzun tutulmus, budceyi asmasi icin yeterince kelime iceriyor ve boylece isaretlenmesi bekleniyor."}'

sleep 3

psql -h localhost -p 5433 -U humetric -d humetric -c \
  "select metric_key, review_status, trace_data->>'budget_exceeded', trace_data->>'measured_input_tokens' from entity_metric where entity_id='e1' order by last_updated desc limit 5;"
```

**Beklenen**: `review_status = pending_review`, `budget_exceeded = true`,
`measured_input_tokens` sayısal bir değer taşıyor. Bütçe **içindeki** bir sinyalde ise
`budget_exceeded` anahtarı hiç bulunmaz (`null` döner) ama `measured_input_tokens` yine
sayısaldır. Sinyal metninin **hiçbir parçası** kesilmiş
değil — worker log'unda gönderilen `user` mesajının tam metni görülebilir.

---

## 5. Kalite regresyonu (A3 — replay karşılaştırması)

```bash
# Gerçek arayüz: --canary-file / --output; --compare-run argüman almaz,
# --output ile verilen dosyayı önceki koşu raporu olarak okur.
.venv/bin/python -m humetric.replay --pack saha-hizmet-isci \
  --canary-file packs/canary/saha-hizmet-isci-canary.yaml --output /tmp/replay_before.json
# ... A3 değişikliklerini uygula ...
.venv/bin/python -m humetric.replay --pack saha-hizmet-isci \
  --canary-file packs/canary/saha-hizmet-isci-canary.yaml \
  --output /tmp/replay_before.json --compare-run --ci
```

**Beklenen**: Fark varsa `hash_diffs` dolu — yani `prompt_hash` değişmiş ve fark buna
atfedilebilir. `--ci` bayrağıyla çıkış kodu `1` gelirse ama `hash_diffs` doluysa bu **beklenen
ve kabul edilen** bir sonuçtur (bkz. `contracts/prompt-composition.md`).

---

## 6. Maliyet farkı

```bash
# Gerçek arayüz: alt-komutlu (run / report / tokens / cleanup / prices).
# Token maliyeti gerçek sağlayıcıyla küçük bir örneklemde ölçülür:
.venv/bin/python scripts/cost_bench.py run --signals 20 --entities 5 \
  --llm real --embed real --out /tmp/cost_before.json      # T002 baseline
# ... tüm fazlar uygulandıktan sonra ...
.venv/bin/python scripts/cost_bench.py run --signals 20 --entities 5 \
  --llm real --embed real --out /tmp/cost_after.json
.venv/bin/python scripts/cost_bench.py report /tmp/cost_before.json /tmp/cost_after.json
```

Altyapı maliyeti (sağlayıcı harcaması olmadan) ayrı koşulur:
`... run --signals 1000 --entities 50 --llm mock --embed mock --out /tmp/infra.json`.

**Beklenen**: Sinyal başına ortalama token maliyeti raporlanır; cache okuma oranının
sıfırdan çıkması sonrası düşüş görülür.

---

## 7-9. Site tarafı doğrulaması (`../humetric-site`)

Bu adımlar **o depoda** çalıştırılır, kendi test komutlarıyla:

```bash
cd ../humetric-site
npm test -- tools.charge.test.ts       # C1 — sınır aşımında kredi düşmediğini doğrular
# Manuel: 60001 karakterlik text (ya da 40001 karakterlik db_schema) ile
# humetric_pack_wizard_start çağır — Zod hatası ve sıfır kredi düşümü bekle.

# C2 — iki turluk bir wizard oturumu ilerlet, ikinci turda cache_read_input_tokens > 0
# C3 — llm_token_usage tablosunda cache kolonlarının input'tan ayrı olduğunu doğrula
```

**Uçtan uca (adım 9)**: `LOCAL_RUN.md` ile tam stack (motor + site) ayağa kaldırılır, MCP
üzerinden bir `humetric_pack_wizard_start` turu ve bir `humetric_signal_chat_start` turu
çalıştırılır; her ikisinin de sınır/caching davranışı gözlemlenir.

---

## Sık karşılaşılan hatalar

- `connection refused ... 5434` → `.env` hâlâ Docker portunu gösteriyor, `LOCAL_RUN.md`'ye göre
  5433 yap (bu özelliğe özgü değil, mevcut bilinen hata).
- `cache_read_tokens` her zaman `NULL` → DeepSeek ve Anthropic yollarında bu bir **hatadır**
  (ikisi de doğrulandı/raporluyor); OpenAI ve Google yollarında `contracts/llm-call-record.md`
  hâlâ "doğrulanacak" diyor, orada `NULL` beklenebilir.
- `cache_write_tokens` DeepSeek'te `NULL` → **beklenen**. Sağlayıcı cache yazma sayısını hiç
  raporlamıyor; sıfır yazmak FR-002'yi ihlal ederdi.
- Migration `022`'den sonra gelmiyor → `down_revision = "022"` kontrol et; `020`'nin diskteki
  hâlini şablon olarak kopyalamadığından emin ol (`research.md` R7).
