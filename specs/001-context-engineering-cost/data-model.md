# Faz 1 — Veri Modeli

**Feature**: `001-context-engineering-cost` · **Tarih**: 2026-09-07

Bu özellik **bir** kalıcı varlığı değiştirir (`llm_call_record`), **bir** JSONB sözleşmesini
genişletir (metrik iz kaydı), **iki** Pydantic modeline alan ekler ve ayrı depoda **bir**
tabloya kolon ekler. Yeni tablo yoktur.

---

## 1. `llm_call_record` — LLM çağrı kaydı (DEĞİŞTİRİLİR)

**Mevcut durum** (`src/humetric/db/models.py:357-381`, migration `020`): Bir LLM çağrısının
kiracı/sinyal/pack/sağlayıcı/model boyutlarıyla **tek bir toplam** token sayısını taşıyor.
Günlük kiracı toplamını tutan `metering_record`'u tamamlar, onun yerini almaz.

### Eklenen alanlar

| Alan | Tip | Null | Anlam |
|---|---|---|---|
| `input_tokens` | `Integer` | evet | Cache'lenmemiş girdi token'ı — tam ücretle faturalanan kısım. |
| `output_tokens` | `Integer` | evet | Üretilen token. |
| `cache_read_tokens` | `Integer` | evet | Cache'ten okunan girdi token'ı. **Sıfırdan büyük olması, cache'in gerçekten çalıştığının tek kanıtıdır.** |
| `cache_write_tokens` | `Integer` | evet | Cache'e yazılan girdi token'ı (ilk çağrıda dolu, sonrakilerde sıfır). |

### Değişmeyen alanlar

`id`, `tenant_id`, `signal_id`, `pack_key`, `pack_version`, `provider`, `model`, `created_at`
ve — özellikle — **`token_count`**.

> **`token_count`'un anlamı değiştirilmez.** Bugün girdi + çıktı toplamı ve okuyucuları var
> (`store.py:495`, pack kullanım raporu). Yeniden anlamlandırmak sessiz davranış değişikliği
> olurdu (`HM-REAS-05`). Toplam kolonu olduğu gibi kalır; yeni alanlar **yanına** eklenir.

### Neden hepsi nullable?

Sağlayıcı bir token türünü raporlamıyorsa alan `NULL` kalır. Sıfır yazmak "cache çalışmadı" ile
"sağlayıcı raporlamıyor"u ayırt edilemez hâle getirir — tam da bu özelliğin çözdüğü körlüğün
yeni bir sürümü olurdu. Ayrıca nullable kolon eklemek `HM-DATA-03`'e göre **GÜVENLİ** bir
migration işlemidir; mevcut satırlar yeniden yazılmaz, tablo kilitlenmez.

### Doğrulama kuralları

- Yazılan her değer negatif olamaz.
- Bir alan ya sağlayıcının raporladığı değerdir ya `NULL`'dur — **hesaplanmış, çıkarsanmış veya
  tahmin edilmiş değer yazılmaz**. Tahmin `context.py`'nin işidir ve iz kaydına gider, buraya
  değil.
- `token_count` bugünkü kuralını korur.

### Tekillik ve tekrar

Satır **çağrı başına** eklenir, güncellenmez. Aynı sinyal yeniden işlenirse ikinci bir satır
oluşur; bu kasıtlıdır — çağrı gerçekten iki kez yapılmış ve iki kez ödenmiştir. Yeniden deneme
(retry) ve format düzeltme (re-ask) çağrıları da ayrı satırlardır; bugünkü davranış budur ve
korunur.

### RLS

Tablo zaten `tenant_id` taşıyor, RLS'i etkin, `humetric_app` grant'ı verilmiş ve politika
gövdesi `022` ile güvenli `NULLIF(current_setting('app.tenant_id', true), '')::bigint` biçimine
yeniden yazılmış. **Politika satır düzeyinde çalışır; kolon eklemesi onu etkilemez, yeni politika
gerekmez.** Bu gerekçe migration docstring'ine yazılır (bkz. `contracts/llm-call-record.md`).

### Yazma yolu

Tek nokta: `services/usage_service.py:65-87` (`_insert_llm_call_record`) ve onu çağıran
`record_llm_tokens`. Üç çağıran vardır — Anthropic yolu (`agents/base.py`), OpenAI/DeepSeek
yolu ve Google yolu (`agents/multi_llm.py`) — ve üçü de yeni alanları geçirecek şekilde
genişler. Alanları toplayamayan yol `None` geçirir.

### Migration

`alembic/versions/023_llm_call_token_breakdown.py` · `down_revision = "022"`

- `upgrade()`: dört nullable kolon ekler.
- `downgrade()`: tam olarak o dört kolonu düşürür — başka hiçbir şeye dokunmaz (`HM-DATA-04`;
  referans şekil `018_usage_record_client_dims.py`).
- Yeni index yok: mevcut iki index (`tenant_id, pack_key` ve `tenant_id, created_at`) bu
  alanların sorgulanacağı erişim yollarını zaten kapsıyor. Yeni bir index ancak ölçüm gerçek
  bir sorgu ihtiyacı gösterirse eklenir.

---

## 2. Metrik iz kaydı (`trace_data`) — bütçe aşımı işareti (GENİŞLETİLİR)

**Mevcut durum**: `worker.py:222-232` her metrik için bir iz sözlüğü kuruyor ve
`Store.upsert_metric` ile `trace_data` JSONB kolonuna yazıyor. Sözlük bugün çıkarım kaydını,
prompt/şema/bağlam hash'lerini, model adını ve iki işaretleyiciyi (`needs_review`,
`source_span_verified`) taşıyor.

### Eklenen alanlar

| Anahtar | Tip | Anlam |
|---|---|---|
| `budget_exceeded` | `bool` | Bu metriği üreten extraction çağrısının bağlamı bütçeyi aştı mı. |
| `measured_input_tokens` | `int` | Çağrı öncesi ölçülen **tahmini** girdi token sayısı. |

**Bu iki alanın taşıyabileceği başka hiçbir şey yoktur.** Sinyal metni, metrik değeri, kullanıcı
verisi ya da herhangi bir kişisel veri parçası iz kaydına **girmez** (Anayasa İlke III;
sözleşme `contracts/trace-budget-flag.md`).

### İnceleme durumuyla ilişkisi

`review_status` bugün `"pending_review" if (fm.needs_review or not span_verified) else None`
(`worker.py:265`). Bütçe aşımı üçüncü koşul olarak eklenir — yeni bir mekanizma icat edilmez,
mevcut kalıp genişletilir.

### Rıza önceliği

Rıza kapısı (`worker.py:196-204`) bütçe işaretlemesinden **önce** çalışır ve rıza reddedilen
metrik için `continue` eder — yani metrik hiç yazılmaz, dolayısıyla işaretlenecek bir satır da
oluşmaz. Bu sıra korunur.

### Geriye dönük uyumluluk

`trace_data` şemasız bir JSONB'dir; okuyucular anahtarın yokluğuna tolerans göstermek
zorundadır. Mevcut satırlarda bu iki anahtar bulunmaz ve **bulunmaması "aşım yok" anlamına
gelir**. Geri doldurma yapılmaz.

---

## 3. `PackPrompts` — pack prompt tanımı (GENİŞLETİLİR)

**Mevcut durum** (`src/humetric/schema.py:468-471`): Tek alan — `extraction: str = ""`.

### Eklenen alanlar

| Alan | Tip | Varsayılan | Anlam |
|---|---|---|---|
| `rubric` | `str` | `""` | Pack'in puanlama ölçütü; cache'lenen sistem bloğuna girer. |
| `examples` | `str` | `""` | Pack'e özgü few-shot çapaları; cache'lenen sistem bloğuna girer. |

### Doğrulama değişikliği

Bu alt modele **`extra="forbid"`** eklenir: bugün bir pack YAML'ına `examples:` yazan kişinin
girdisi sessizce düşürülüyor. Artık tanınmayan anahtar açık doğrulama hatası verir.

> **Kapsam uyarısı:** `extra="forbid"` **yalnızca `PackPrompts`'a** konur.
> `PackDefinition`'ın tamamına koymak, bugün ek anahtar taşıyan mevcut pack'leri kırar.

### Anlamsal değişiklik

`extraction` alanının anlamı değişir: bugün **taban promptu ezen** tam bir sistem promptu;
bundan sonra **taban bloğa eklenen** alan çerçevesi. Mevcut pack'lerin YAML'ı değişmez, ama
üretilen sistem promptu değişir — bu kasıtlıdır ve `HM-REAS-05` kapsamında sözleşme belgesine
sürüm notu olarak yazılır.

---

## 4. `QueryRequest` — sorgu isteği (GENİŞLETİLİR)

**Mevcut durum** (`src/humetric/schema.py:332-340`): `entity_type`, `rank_by`, `filters`,
`free_text_query`, `top_k`, `include_reasoning`.

### Eklenen alan

| Alan | Tip | Varsayılan | Anlam |
|---|---|---|---|
| yeniden sıralama anahtarı | `bool` | **`True`** | LLM yeniden sıralayıcısının çalışıp çalışmayacağı. |

Varsayılan `True` olmak **zorundadır**: `False` yapmak mevcut tüketicilerin yanıt sıralamasını
haber vermeden değiştirir (`HM-REAS-05`). Alan adı, dosyanın mevcut kalıbına uyarak snake_case
ve camelCase takma adıyla birlikte tanımlanır.

Kapalıyken sonuçlar hibrit aramanın kendi skoruyla döner; `QueryResponse` ve `RankedResult`
şekilleri **değişmez** — yalnızca `reasoning` alanı doğal olarak boş kalır.

---

## 5. Bağlam ölçümü — kalıcı olmayan yapı

`src/humetric/context.py`'nin ürettiği ölçüm bir tabloya yazılmaz. İki tüketicisi vardır:
bütçe kapısının kararı ve iz kaydına yazılan `measured_input_tokens`. Arayüzü
`contracts/context-measurement.md` tanımlar.

---

## 6. Ayrı depo — `llm_token_usage` (`../humetric-site`)

**Mevcut durum** (`backend/schema.sql:179-189`): `user_id`, `day`, `provider`, `input_tokens`,
`output_tokens`, `calls`; birincil anahtar `(user_id, day, provider)`.

**Sorun**: `agentic/tokenUsage.ts:60-64` cache yazma ve cache okuma token'larını `input`'a
**katlıyor**. Kaydedilen tek sayı içinde cache'in payı görünmez; dolayısıyla site tarafında
caching'in işe yarayıp yaramadığı ölçülemez.

**Değişiklik**: cache okuma ve cache yazma için ayrı sütunlar eklenir; katlama kaldırılır.
Mevcut satırlar geri doldurulmaz — `NULL`/sıfır, "o dönemde ölçülmüyordu" demektir.

Bu değişiklik **bu depoda uygulanmaz**; `../humetric-site` projesine aittir (Anayasa İlke VI).

---

## Varlık ilişkileri (bu özelliğin dokunduğu kısım)

```
tenant ─┬─< llm_call_record   (çağrı başına bir satır; token muhasebesi burada)
        ├─< metering_record   (günlük kiracı toplamı — DEĞİŞMEZ)
        ├─< signal ──< task   (extraction çağrısını doğuran iş)
        └─< entity ──< entity_metric
                          └── trace_data (JSONB) ← bütçe aşımı işareti burada
metric_pack.definition (JSONB)
        └── prompts (PackPrompts) ← rubric / examples burada
```

## Kapsam dışı bırakılan model değişiklikleri

| Değişiklik | Neden bu turda yapılmıyor |
|---|---|
| `SignalCreate.text` sınırının düşürülmesi | Mevcut API tüketicileri için kırıcı. Ölçüm gerçek dağılımı gösterdikten sonra ayrıca değerlendirilir. |
| Sinyal parçalama (chunking) için parça varlığı | Parçaları mevcut birleştirmeyle toplamak yanlış olurdu: güven ağırlıklı ortalama + artan kanıt sayacı, tek sinyalin parçalarını birden fazla bağımsız kanıt gibi sayar. Ayrı birleştirme semantiği ister. |
| Ayrı token-detay tablosu | Birebir ilişkili, aynı yaşam döngüsündeki alanlar için ikinci tablo yalnızca join ve yeni bir RLS yüzeyi getirir (`HM-REAS-01`). |
| Dead-letter tablosu | Orkestrasyon dayanıklılığı bu turda kapsam dışı; teknik borç olarak kayıtlı. |
| `token_count`'un yeniden anlamlandırılması | Mevcut okuyucuları var; sessiz davranış değişikliği olurdu. |
