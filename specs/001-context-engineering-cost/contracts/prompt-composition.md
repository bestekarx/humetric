# Sözleşme: Prompt kompozisyonu — ezme yerine birleştirme

**Feature**: `001-context-engineering-cost` · Faz: A3 + B1

## Bugünkü davranış (değişecek)

`agents/extractor.py:26`:

```python
system = pack_prompt or _DEFAULT_SYSTEM
```

Pack promptu tanımlıysa taban prompt **tamamen** atılır. Ayrıca ölçek sınırı, `source_span`
zorunluluğu ve belirsizlik kuralı bugün **user** mesajının içinde
(`extractor.py:64-72`) — sistem bloğunda değil, dolayısıyla cache'lenmez ve her çağrıda
yeniden gönderilir.

## Hedef davranış

Sistem bloğu her zaman şu sırayla, birleştirilerek render edilir:

1. **Değişmez taban** — rol tanımı, çıktı alanları, ölçek sınırı (`-1.0..1.0`), `source_span`
   zorunluluğu, belirsizlikte `needs_review`/`confidence: 0.0` kuralı, doğru/yanlış JSON
   çıktı örneği. (Bugünkü `prompts/extractor-default.md`'nin içeriği + user mesajından
   taşınan kalibrasyon kuralları.)
2. **Pack'in alan çerçevesi** — `PackPrompts.extraction` (anlamı değişir: artık ekleme, ezme
   değil — bkz. `data-model.md` §3).
3. **Pack'in rubric'i** — `PackPrompts.rubric` (yeni alan).
4. **Pack'in örnekleri** — `PackPrompts.examples` (yeni alan).
5. **Allowed-keys bloğu** — bugünkü davranış korunur (`extractor.py:32-53`).

Pack hiçbir prompt alanı tanımlamıyorsa (bugünkü varsayılan durum), sonuç bugünküyle
**aynıdır** — taban blok zaten `_DEFAULT_SYSTEM`'in kendisiydi.

## `PackPrompts` doğrulaması

`extra="forbid"` **yalnızca `PackPrompts` alt modelinde**. Tanınmayan bir anahtar (örn.
yanlış yazılmış `exmaples:`) artık pack kaydı/güncellemesinde açık doğrulama hatası verir;
sessizce düşürülmez.

## Çıktı kesilmesi tespiti

`resp.stop_reason == "max_tokens"` durumu yakalanır. Bu durumda:
- Sonuç sessizce `metrics: []` olarak **dönmez**.
- Açık bir hata/istisna fırlatılır ki çağıran (worker) bunu "model hiçbir şey bulamadı"dan
  ayırt edebilsin.

`MAX_METRICS_PER_PACK` sınırı bu turda **kaldırılmaz** — tespit yerleşmeden koruma
kaldırılmaz.

## Cache kanıtı (B1)

A3 prefix'i büyüttükten sonra:
1. Yapılandırılmış modelin minimum cache eşiği ile büyüyen prefix'in boyutu karşılaştırılır
   (A1'in ölçüm modülüyle, çevrimdışı ya da log üzerinden).
2. Eşiği geçiyorsa: cache işaretlemesi (`cache_control`) bugünkü gibi kalır.
3. Eşiği geçmiyorsa: ya prefix bilinçli olarak büyütülür (ör. daha fazla few-shot çapası) ya
   da o model için cache işaretlemesi **açıkça kapatılır** ve bu durum log'a/dokümana yazılır.
   Sessiz kalmak seçenek değildir.
4. **Açılış kontrolü (FR-011):** prefix her açılışta ölçülür; yapılandırılmış eşiğin altındaysa
   `WARNING` loglanır. Tek seferlik denetim değildir — model ya da prompt sonradan değiştiğinde
   durum yeniden görünür olur.
5. **Doğrulama iki parçadır.** Bu depoda `tests/` gitignore'dadır ve CI sahte anahtarla koşar
   (`ci.yml`, `ANTHROPIC_API_KEY: sk-test`); dolayısıyla "CI'da `cache_read_tokens > 0`" bir
   kabul kriteri olarak kurulamaz. Yerine:
   - **CI'da, anahtarsız kalıcı kontrol:** commit edilen `scripts/verify_cache_prefix.py`
     prefix'i iki kez kurar, bayt-özdeşliği ve eşiği geçtiğini doğrular. Sağlayıcıya çağrı
     yapmaz. Cache regresyonuna karşı asıl kalıcı koruma budur.
   - **Yerel, gerçek anahtarla manuel doğrulama:** aynı pack ile art arda iki extraction çağrısı;
     ikincisinin `llm_call_record.cache_read_tokens > 0` olduğu elle doğrulanır
     (`quickstart.md` adım 3).

## Prefix stabilite denetimi

- Allowed-keys bloğunun satır sırası, pack'in metrik tanımlarının sırasına bağlıdır. Aynı pack
  sürümü için bu sıra sabittir (pack YAML'ındaki liste sırası değişmedikçe).
- `tool["input_schema"]` (`schema.model_json_schema()`) her çağrıda bayt-özdeş serileştirilir
  (Pydantic v2 deterministiktir). Kalıcı test bu iki noktayı da örtük olarak doğrular — sistem
  bloğu ve tool şeması sabitse, iki ardışık çağrı arasındaki tek fark user mesajıdır ve cache
  yalnızca sistem+tool prefix'ini kapsar.

## Sağlayıcı başına cache

`cache_control` işaretlemesi bugün yalnızca Anthropic yolunda var. Diğer sağlayıcılara
eklenmeden önce, o sağlayıcının cache semantiği (varsa) **ölçülerek** doğrulanır — varsayım
yazılmaz. Soyutlama `multi_llm.py`'nin sağlayıcı dallarının **içinde** kalır; ortak bir arayüz
dayatılmaz (research R6, Anayasa İlke II).

**Doğrulanmış: DeepSeek (2026-09-08, canlı ölçüm).** Cache **otomatiktir** — istekte hiçbir
işaretleme yok ve ikinci çağrı cache'ten okuyor. Ölçülen: 762 token'lık prefix, birinci çağrı
`cache_hit=0 / cache_miss=762`, ikinci çağrı `cache_hit=640 / cache_miss=122`; blok
granülaritesi 64 token. **Bu dala `cache_control` eklenmeyecektir** (FR-021): istekte karşılığı
yoktur. Bu daldaki iş yalnızca *okuma* tarafıdır — hit/miss alanlarının
`llm_call_record`'a ayrı ayrı yazılması.

**Ölçülen: Google (2026-09-09, dört prefix boyutunda canlı ölçüm).** Örtük (implicit) caching
var, işaretleme gerektirmez, **ama iki koşulu birden taşır:**

| prefix | 1. çağrı | 2. çağrı | 3. çağrı |
|---|---|---|---|
| 736 tok | 0 | 0 | 0 |
| 1.339 tok | 0 | 0 | 0 |
| 2.529 tok | 0 | **2.026** | 0 |
| 4.209 tok | 0 | **4.069** | 0 |

1. **Eşik var.** Cache 1.339 token'da hiç devreye girmedi, 2.529'da girdi — eşik bu ikisinin
   arasındadır. Bu, Google'ı DeepSeek'e değil (64 token'lık blok, eşiksiz) eşik gerektiren
   sağlayıcı sınıfına koyar. **Ölçülen pack prefix'lerinin 11'den 10'u bu eşiğin altındadır**
   (539–1.450 token; yalnızca en büyük pack 2.428 ile sınıra yaklaşıyor) — yani A3'ün prefix
   büyütmesi Google yolunda da doğrudan işe yarar.
2. **Deterministik değil.** Her iki ölçümde de cache 2. çağrıda okundu ve **3. çağrıda
   sıfıra düştü**. Örtük cache "best-effort"tur, garanti değildir.

**Sonuç — kabul kriteri için:** "ikinci çağrıda `cache_read_tokens > 0`" Google yolunda
**kararsız (flaky) bir assert'tir** ve bir kapı olarak kullanılamaz. Google için kanıt, tek bir
çağrı çiftinde değil, **birden çok çağrı üzerinde cache okuma oranının sıfırdan büyük olması**
biçiminde kurulmalıdır. CI'daki kalıcı koruma zaten anahtarsız prefix kontrolüdür
(`scripts/verify_cache_prefix.py`); bu ölçüm o tercihi doğruluyor.

**Sonuç: bu sözleşmenin cache iddiası sağlayıcıya göre ikiye ayrılır.** İşaretleme gerektiren
yolda prefix'i eşiğin üstüne çıkarmak cache'i **ilk kez** çalıştırır; otomatik cache'leyen yolda
cache zaten çalışmaktadır ve büyüyen prefix yalnızca cache'lenen payı artırır. Prompt
kompozisyonu düzeltmesinin (A3) **birincil gerekçesi her iki durumda da kalitedir** — pack
override'ının değişmez kuralları düşürmesi bir hatadır, cache'ten bağımsız olarak düzeltilir.

## Kabul kriterleri

1. Pack promptu tanımlı bir pack'te üretilen sistem bloğu, değişmez bloğun tamamını içerir
   (ölçek, `source_span`, kalibrasyon, örnek).
2. Tanınmayan bir prompt alanı pack kaydında/güncellemesinde reddedilir.
3. `stop_reason == "max_tokens"` durumunda açık hata alınır; sessiz boş liste dönmez.
4. `scripts/verify_cache_prefix.py` CI'da yeşil: prefix bayt-özdeş ve eşiğin üstünde.
5. Yerel manuel doğrulamada, aynı pack'le ardışık iki çağrının ikincisinde
   `cache_read_tokens > 0`. Bu kanıt **sağlayıcıya göre farklı şey ispatlar**: otomatik
   cache'leyen sağlayıcıda ölçümün doğru kaydedildiğini, işaretleme gerektiren sağlayıcıda
   cache'in devreye girdiğini (bkz. `spec.md` SC-003).
6. `replay.py --compare-run` bu fazın ürettiği metrik farkını `prompt_hash` değişimiyle
   ilişkilendirir — "hash değişti, kasıtlı" sınıfına düşer, "açıklanamayan sürüklenme"ye değil.

## Anayasa denetimi

- **İlke II**: Marka model adı geçmez; sağlayıcı farkları dağıtıcının kendi dalında kalır.
- **İlke VI**: Değişmez blok `prompts/extractor-default.md` dosyasında kalır, Python koduna
  gömülmez.
- **HM-REAS-05 (sessiz davranış değişikliği)**: Bu sözleşme metrik değerlerini değiştirebilir.
  Kabul kriteri #5 bunun *kasıtlı ve atfedilebilir* olduğunu garanti eder — sessiz değildir.
