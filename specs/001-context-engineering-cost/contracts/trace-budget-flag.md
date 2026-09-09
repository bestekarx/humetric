# Sözleşme: Bütçe aşımı işaretleme (`trace_data`)

**Feature**: `001-context-engineering-cost` · Faz: A2

## Davranış: "işaretli devam"

Bir extraction çağrısının bağlamı (`context.py:measure_extract_inputs()` ile ölçülen)
`config.py`'deki `EXTRACT_INPUT_TOKEN_BUDGET`'ı aşarsa:

1. Çağrı **yine de yapılır** — iş düşürülmez, sinyal reddedilmez.
2. Üretilen metrikler mevcut inceleme kalıbına göre işaretlenir: `review_status` hesaplaması
   `fm.needs_review or not span_verified` koşuluna **üçüncü bir koşul** eklenir:
   `budget_exceeded`.
3. `trace_data` JSONB'sine `budget_exceeded: true` yazılır.

### Alanların yazılma kuralı (kesin)

| Anahtar | Ne zaman yazılır |
|---|---|
| `measured_input_tokens` | **Her metrikte, her zaman** — bütçe aşılsın ya da aşılmasın (A1'in ölçüm çıktısı; bkz. `contracts/context-measurement.md` § Tüketiciler 2). |
| `budget_exceeded` | **Yalnızca aşımda.** Bütçe içindeki bir metrikte anahtar hiç bulunmaz — `false` değeriyle de bulunmaz. |

## Kesin yasaklar

- **Metin kırpılamaz.** Sinyal metninin hiçbir parçası bütçe nedeniyle kesilip modele
  gönderilemez.
- **Çağrı düşürülemez.** Bütçe aşımı sert bir hata değildir; sinyal zaten kabul edilmiş ve
  kuyruğa alınmıştır.
- **`trace_data`'ya sinyal metni, metrik değeri veya başka kişisel veri yazılamaz.** Yalnızca
  `budget_exceeded` (bool) ve `measured_input_tokens` (int) — kapalı liste, genişletilemez
  olmadan bu sözleşme güncellenmeden.

## Rıza kapısıyla sıralama

Rıza kapısı (`worker.py:196-204`) bütçe işaretlemesinden **önce** çalışır. Rıza reddedilen
metrik için döngü `continue` eder — metrik hiç yazılmaz, `trace_data` da hiç oluşmaz. Bütçe
aşımı işaretlemesi, rıza kapısını geçmiş metrikler için devam eden mevcut akışın bir adımıdır.

## Kapsam sınırları (bu turda yapılmaz)

- **Chunking yapılmaz.** Uzun sinyali parçalayıp ayrı ayrı işlemek, mevcut birleştirme
  fonksiyonuyla (`curator.py:finalize_merge`) toplanamaz: o fonksiyon güven ağırlıklı ortalama
  alır ve kanıt sayacını (`source_count`) artırır — tek sinyalin parçaları birden fazla
  bağımsız kanıt gibi sayılır ve güven suni olarak yükselir. Chunking gerçekten istenirse ayrı
  bir birleştirme semantiği gerektirir; bu özelliğin kapsamı dışındadır.
- **`SignalCreate.text`'in 300.000 karakter sınırı değişmez.** Bu sözleşme yalnızca bütçe
  aşıldığında ne olacağını tanımlar; girdi sınırının kendisini düşürmez.

## Kabul kriterleri

1. Bütçeyi aşan bir sinyal ingest edilip işlendiğinde: çağrı yapılır, üretilen metriklerin
   `review_status` alanı `pending_review`, `trace_data.budget_exceeded` `true` ve
   `trace_data.measured_input_tokens` ölçülen sayıya eşittir.
2. Bütçe içindeki bir sinyalde `trace_data` içinde `budget_exceeded` anahtarı **bulunmaz**
   (var olup `false` değil — anahtar yok; mevcut satırlarla tutarlı), ancak
   `measured_input_tokens` **bulunur** ve sayısaldır.
3. Bütçe aşımı olsun olmasın, modele gönderilen `user` mesajının içeriği bütçe kapısından
   etkilenmez — bayt bazında aynıdır.
4. Rıza reddedilen bir metrikte hiçbir `trace_data` satırı oluşmaz (mevcut davranış).

## Anayasa denetimi

- **İlke III**: `trace_data`'ya yazılan iki alan kapalı bir listedir ve kişisel veri
  taşımaz; rıza kapısı sıralaması korunur.
- **HM-REAS-02 (sınır durumu)**: Bu sözleşmenin ana konusu tam olarak budur — bütçe aşımı bir
  sınır durumudur ve açıkça ele alınır, örtük olarak yutulmaz.
