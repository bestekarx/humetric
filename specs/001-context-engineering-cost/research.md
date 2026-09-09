# Faz 0 — Araştırma: Bağlam Mühendisliği ve Maliyet Optimizasyonu

**Feature**: `001-context-engineering-cost` · **Tarih**: 2026-09-07

Bu belge, plan yazılmadan önce açık kalan her soruyu kapatır. Her başlık:
**Karar** → **Gerekçe** → **Değerlendirilen alternatifler**. Kod referansları,
`main` üzerinde `38657c7` commit'indeki hâlleriyle **doğrulanmıştır**.

---

## R1 — Cache'lenen prefix gerçekten minimumun altında mı?

**Karar: Evet, altında — ve bu sessiz bir kayıptır. Doğrulandı.**

Ölçülen prefix üç parçadan oluşuyor (`agents/base.py:107-123`, `_build_tool_and_system`):

| Parça | Kaynak | Boyut |
|---|---|---|
| Sistem promptu | `prompts/extractor-default.md` | 1.357 karakter |
| Tool şeması | `ExtractionResult.model_json_schema()` | ~700 karakter |
| Pack allowed-keys bloğu | `agents/extractor.py:32-53`, sisteme eklenir | pack'e göre değişken |

Toplam kabaca 1.000 token mertebesinde. Sağlayıcının minimum cache'lenebilir prefix eşiği
**modele göre değişir ve kuşaklar arasında monoton değildir** — 512, 1.024, 2.048 ve 4.096
token değerleri kullanımda. `config.py:46`'daki varsayılan agent modelinin (extractor'ın
kullandığı model) eşiği **4.096 token**. Prefix bu eşiğin belirgin biçimde altında.

Eşiğin altındaki bir prefix **hata vermez**: istek başarıyla döner, `cache_control` yok sayılır
ve cache oluşturma token'ı sıfır kalır. Bugün bunu görmenin hiçbir yolu yok, çünkü
`base.py:167` yalnızca `input_tokens + output_tokens` topluyor.

**Gerekçe:** Bu, planın merkezî iddiası. Ölçülmeden doğrulanamazdı; ölçüldü.

**Alternatifler:**
- *Prefix'i suni olarak şişirmek* — reddedildi. Doldurma metni hem kaliteyi düşürür hem her
  çağrıda yazma primi ödetir. Prefix ancak **işe yarar** içerikle büyütülmelidir: değişmez
  kurallar, rubric ve few-shot çapaları (bkz. R3).
- *Sadece daha büyük eşikli modeli değiştirmek* — reddedildi. Model seçimi kiracının BYOK
  kararıdır; motor bir modeli dayatamaz (Anayasa İlke II).

---

## R2 — Prefix zaten bayt-özdeş mi? (cache'in ön koşulu)

**Karar: Büyük ölçüde evet, ama iki nokta implementasyonda denetlenmelidir.**

Cache prefix eşleşmesidir: prefix'in herhangi bir yerindeki tek bayt değişikliği sonrasındaki
her şeyi geçersiz kılar. Render sırası `tools` → `system` → `messages`.

Doğrulanan iyi durumlar:
- Sistem promptu import zamanında dosyadan bir kez okunuyor (`agents/__init__.py:_load_prompt`)
  — zaman damgası, UUID veya istek kimliği yok.
- Sinyal metni ve varlık bağlamı **user** mesajında (`extractor.py:56-74`), yani son
  breakpoint'ten sonra — doğru yer.

Denetlenecek iki nokta:
1. **Allowed-keys bloğunun sıralaması** (`extractor.py:32-53`) pack'teki metrik sırasına bağlı.
   Aynı pack sürümü için sıra sabit olduğu sürece sorun yok; pack güncellemesi prefix'i
   değiştirir ve bu **doğrudur** (farklı pack = farklı prefix).
2. **Tool şemasının serileştirmesi** (`schema.model_json_schema()`) her çağrıda bayt-aynı
   üretilmeli. Pydantic v2 bunu deterministik üretir, ancak kalıcı test bunu kilitlemelidir —
   bir gün bir alan `set` üzerinden türetilirse sessizce bozulur.

**Gerekçe:** Cache regresyonu sessizdir; istek başarılı olmaya devam eder, yalnızca fatura
büyür. Bu yüzden tespit tek seferlik bakış değil, kalıcı kontrol olmalıdır.

**Alternatifler:**
- *Manuel gözle inceleme* — reddedildi. Tam da sessizce bozulan sınıfa giriyor.

---

## R3 — Prompt kompozisyonu: ezme mi, birleştirme mi?

**Karar: Değişmez taban blok her zaman render edilir; pack yalnızca alan çerçevesi ekler.**

`agents/extractor.py:26` bugün `system = pack_prompt or _DEFAULT_SYSTEM` yazıyor. Pack promptu
tanımlıysa taban prompt **tamamen** atılıyor — kalibrasyon kuralları, "emin değilsen
`needs_review`" talimatı ve doğru/yanlış çıktı örneği dahil.

Ayrıca keşifte ortaya çıkan ve plan notunda olmayan bir bulgu: **değişmez kuralların bir kısmı
zaten yanlış yerde.** Ölçek sınırı, `source_span` zorunluluğu ve belirsizlik kuralı bugün
**user** mesajının içinde (`extractor.py:64-72`), sistem bloğunda değil. Yani hem her çağrıda
yeniden gönderiliyorlar (cache'lenmiyorlar) hem de cache'lenebilecek prefix'i büyütmüyorlar.
Bunları sistem bloğuna taşımak tek hamlede iki sorunu birden çözer: prefix eşiğe yaklaşır ve
kural tekrarı ortadan kalkar.

Hedef kompozisyon (sırayla, hepsi sistem bloğunda):
1. Değişmez taban: rol, çıktı alanları, ölçek, `source_span` zorunluluğu, kalibrasyon kuralı.
2. Değişmez çıktı-biçimi çapası: doğru ve yanlış JSON örneği (bugün taban promptta var).
3. Pack'in alan çerçevesi (`prompts.extraction`) — **ekleme**, ezme değil.
4. Pack'in rubric'i ve örnekleri (yeni alanlar).
5. Allowed-keys bloğu.

**Gerekçe:** `pipeline.md:258-263` bu kaybın bir pack'te fiilen yaşandığını zaten kaydetmiş.
Ayrıca `c802a95` commit'inin eklediği kalibrasyon kuralları pack override'ında kayboluyor —
yani düzeltilmiş bir hata, pack yazan herkes için geri geliyor.

**Alternatifler:**
- *Pack'e "tabanı ez" seçeneği bırakmak* — reddedildi. Kalibrasyon ve `source_span` motorun
  veri sözleşmesidir, pack'in tercihi değil. Ezilebilir olması bugünkü hatanın ta kendisi.
- *Taban promptu pack YAML'ına kopyalamak* — reddedildi. Tekrarlanan sözleşme, sürüklenen
  sözleşmedir; 11 pack'in her birinde ayrı ayrı eskir.

---

## R4 — Çıktı token sınırına takılma nasıl tespit edilir?

**Karar: Yanıtın durma nedeni token sınırıysa açık hata üretilir.**

Bugünkü durum bir semptom yaması: `MAX_METRICS_PER_PACK = 7` (`config.py:145`) var, çünkü
`MAX_TOKENS = 2048` (`config.py:132`) çıktıyı ortadan kesince kırpılmış tool çağrısı yine de
şemaya karşı doğrulanıyor ve sonuç sessizce boş metrik listesi oluyor. `config.py:136-144`
bunu yorumda açıkça anlatıyor.

Sağlayıcı yanıtları durma nedenini raporlar (`end_turn`, `max_tokens`, `tool_use`, ...). Token
sınırına takılma tespit edilebilir bir durumdur ve boş sonuçtan ayırt edilmelidir: "model hiçbir
metrik bulamadı" ile "modelin cevabı ortadan kesildi" aynı şey değildir.

**Gerekçe:** `HM-REAS-02` (sınır durumu) — bugün ikisi de aynı sonuca varıyor ve hangisinin
gerçekleştiği kayıtlarda görünmüyor.

**Alternatifler:**
- *`MAX_TOKENS`'ı büyütmek* — tek başına reddedildi. Sınırı iter ama tespit boşluğunu kapatmaz;
  yeter ki bir gün yine takılsın, aynı sessiz boşluk geri gelir. Tespit önce gelir, sınır ayarı
  ölçümden sonra ayrı bir karardır.
- *`MAX_METRICS_PER_PACK`'i kaldırmak* — bu turda reddedildi. Tespit yerleşmeden koruma
  kaldırılmaz.

---

## R5 — Token tahmini nasıl yapılacak?

**Karar: Çalışma zamanında yerel ve deterministik tahmin; kalibrasyon çevrimdışı, sağlayıcının
kendi sayım ucuyla.**

Çalışma zamanında sinyal başına sağlayıcının sayım ucuna ağ çağrısı **eklenmez**: her sinyale
bir ek istek gecikme ve yeni bir hata yüzeyi demektir, üstelik bütçe kapısı çağrıdan *önce*
çalışacağı için bu maliyet her sinyale biner.

Yerel tahminci karakter tabanlı ve sağlayıcı/dil duyarlı bir katsayı kullanır. Katsayı,
`scripts/` altındaki bir kalibrasyon aracıyla gerçek örnek metinler üzerinde sağlayıcının sayım
ucuna karşı ölçülür ve `config.py`'ye varsayılan olarak yazılır. Araç sapmayı raporlar; sapma
kabul edilebilir bandın dışına çıkarsa katsayı güncellenir.

**Gerekçe:** Bütçe kapısı bir *tahmin* kapısıdır, muhasebe değil. Gerçek muhasebe zaten
sağlayıcının döndürdüğü kullanım alanlarından geliyor (R6). Tahmincinin görevi "bu sinyal
olağandışı büyük mü" sorusuna ucuz ve deterministik cevap vermek.

**Alternatifler:**
- *OpenAI tokenizer kütüphanesi (`tiktoken` vb.)* — **reddedildi.** Farklı bir sağlayıcının
  tokenizer'ı; tipik metinde belirgin şekilde eksik sayar, kodda ve İngilizce dışı metinde çok
  daha fazla. HuMetric'in sinyalleri büyük ölçüde Türkçe — sapma en kötü olduğu yerde olur.
- *Her sinyalde gerçek sayım ucu* — reddedildi (yukarıdaki gecikme/hata gerekçesi).
- *Yalnızca karakter sınırı, token hiç sayılmasın* — reddedildi. Karakter, token maliyetiyle
  dile göre değişen bir oranda ilişkilidir; bütçe token cinsinden konuşmalıdır çünkü fatura
  token cinsindedir.

---

## R6 — Cache ve token alanları sağlayıcı başına nasıl okunacak?

**Karar: Anthropic yolu şimdi uygulanır ve doğrulanmıştır; diğer sağlayıcılar için alan adları
implementasyon anında sağlayıcı dokümanından doğrulanır, doğrulanamayan alan `NULL` bırakılır.**

| Sağlayıcı | Durum | Uygulama |
|---|---|---|
| Anthropic (`agents/base.py`) | **Doğrulandı.** Kullanım nesnesi girdi, çıktı, cache-yazma ve cache-okuma token'larını ayrı alanlarda döndürüyor. | Dört alan da okunur ve kaydedilir. |
| OpenAI / DeepSeek (`multi_llm.py:296-299`) | Doğrulanmadı. Bugün yalnızca istem + tamamlama token'ları toplanıyor; cache alanı okunmuyor. | İmplementasyonda sağlayıcı dokümanından doğrulanacak; alan yoksa `NULL`. |
| Google (`multi_llm.py:420-423`) | Doğrulanmadı. Bugün yalnızca istem + aday token'ları toplanıyor. | Aynı — doğrula, yoksa `NULL`. |

`cache_control` işaretlemesi bugün **yalnızca** Anthropic yolunda var (`base.py:115-119`);
diğer sağlayıcı dallarında hiç yok. Her sağlayıcının cache semantiği farklıdır (kimi otomatik,
kimi açık işaretleme ister); bu yüzden soyutlama tek bir ortak arayüze zorlanmaz,
`multi_llm.py`'nin sağlayıcı dalları içinde kalır.

**Gerekçe:** "Alan yoksa `NULL` bırak, uydurma" kullanıcının açık kararıdır ve doğrudur: sıfır
yazmak "cache çalışmadı" ile "sağlayıcı raporlamıyor"u ayırt edilemez hâle getirir — tam da bu
özelliğin çözmeye çalıştığı körlüğün yeni bir sürümü olur.

**Alternatifler:**
- *Dört sağlayıcı için ortak bir cache soyutlaması* — reddedildi (`HM-REAS-01`, yanlış
  soyutlama). Semantikleri farklı; ortak arayüz en küçük ortak paydayı dayatır ve
  Anthropic'in açık breakpoint yerleşimini kaybettirir.
- *Cache alanlarını tek bir toplam kolona katlamak* — reddedildi. Site tarafında bugün tam
  olarak bu yapılıyor (`agentic/tokenUsage.ts:62`) ve sonuç, cache'in işe yarayıp yaramadığının
  ölçülememesi.

---

## R7 — Token kolonları nereye eklenecek? RLS gerekiyor mu?

**Karar: Mevcut `llm_call_record` tablosuna kolon eklenir. Yeni RLS politikası gerekmez —
ama bu, denetlenip yazıya dökülerek geçilmesi gereken bir kapıdır.**

`HM-DATA-02` yeni bir `tenant_id` tablosunun RLS politikasını **aynı** migration'da almasını
şart koşuyor. Burada yeni tablo oluşturulmuyor: `llm_call_record` zaten var (`020`), zaten
`tenant_id` taşıyor, RLS'i etkin ve `humetric_app` grant'ı verilmiş, politika gövdesi de `022`
tarafından güvenli biçime (`NULLIF(...)`) yeniden yazılmış. Politika satır düzeyinde çalışır ve
kolon eklemesinden etkilenmez.

Uyarı (kullanıcının notunu doğruluyoruz): `020_llm_call_record.py`'nin **diskteki hâli** hâlâ
çıplak `current_setting(...)::bigint` biçimini taşıyor. Çalışan veritabanında bu biçim `022` ile
düzeltilmiş durumda, ama dosya bir şablon olarak **kopyalanmamalıdır**. Yeni migration için
doğru referans `022`'dir.

Migration risk sınıfı `HM-DATA-03`'e göre: nullable kolon ekleme → **GÜVENLİ**. `downgrade()`
tam olarak eklediği kolonları düşürür (`HM-DATA-04`; referans şekil `018`).

**Gerekçe:** Kural mekanik olarak uygulanmaz; hangi durumda geçerli olduğu okunur. Yine de
karar planda ve migration docstring'inde açıkça yazılmalıdır ki bir sonraki okuyucu atlandığını
sanmasın.

**Alternatifler:**
- *Ayrı bir token-detay tablosu* — reddedildi (`HM-REAS-01`). Birebir ilişkili, aynı yaşam
  döngüsüne sahip alanlar için ikinci tablo yalnızca join ve yeni bir RLS yüzeyi getirir.
- *`token_count` kolonunu yeniden anlamlandırmak* — reddedildi. Mevcut okuyucular var
  (`store.py:495` pack kullanım raporu); anlamı değiştirmek sessiz davranış değişikliğidir
  (`HM-REAS-05`). Toplam kolonu olduğu gibi kalır, yenileri yanına eklenir.

---

## R8 — Bütçe aşımında ne olacak?

**Karar: "İşaretli devam" — çağrı yapılır, metrik incelemeye alınır, iz kaydına ölçülen token
sayısıyla aşım yazılır. Kırpma yok, sert hata yok.**

Bu kalıp icat edilmiyor; `worker.py:229-232, 265` içinde zaten iki kez kullanılıyor:
`needs_review` ve `source_span_verified` aynı iz sözlüğüne yazılıyor ve `review_status`
`pending_review` oluyor. Bütçe aşımı üçüncü işaretleyicidir.

**Gerekçe:** Sessiz kırpma, ölçmeye çalıştığımız sorunu daha kötü bir biçimde geri getirir —
bağlam sessizce kaybolur ve sonuç yine de "başarılı" görünür. Sert hata ise mevcut API
tüketicilerini kırar ve zaten ödenmiş bir işi çöpe atar.

**Alternatifler:**
- *Kırpma* — reddedildi (yukarıdaki gerekçe; ayrıca `Common Pitfalls`'ta "içerik sessizce
  kırpılmaz" ilkesi).
- *Çağrıyı reddetme* — reddedildi. Bütçe bir *uyarı eşiği*; sinyal zaten kabul edilmiş ve
  kuyruğa alınmış durumda.
- *Chunking* — bu turda reddedildi. Parçaların sonuçlarını mevcut birleştirme fonksiyonuyla
  toplamak **yanlış olurdu**: `curator.py:76-78` güven ağırlıklı ortalama alıyor ve
  `worker.py:191` kanıt sayacını (`source_count`) artırıyor — yani tek bir sinyalin parçaları
  birden fazla bağımsız kanıt gibi sayılır ve güven suni olarak yükselir. Chunking ayrı bir
  birleştirme semantiği ister; ayrı iş.

---

## R9 — Yeniden sıralama nasıl koşullu hâle gelecek?

**Karar: İstek gövdesine opsiyonel bir anahtar eklenir; varsayılan bugünkü davranıştır.**

`api.py:1637-1647` bugün her `/v1/query` çağrısında LLM sıralayıcısını koşulsuz çalıştırıyor.
`agents/ranker.py:30-31` yalnızca aday listesi boşsa erken dönüyor; onun dışında her zaman bir
LLM çağrısı yapılıyor. `QueryRequest` (`schema.py:332-340`) bugün böyle bir anahtar taşımıyor.

Varsayılan `true` (bugünkü davranış) olmak zorundadır: varsayılanı `false` yapmak, mevcut
tüketicilerin yanıt sıralamasını haber vermeden değiştirir — `HM-REAS-05`.

**Gerekçe:** Ucuz sorgular LLM'e hiç gitmemeli. Hibrit arama zaten kendi skorunu üretiyor
(`Store.hybrid_search_entities`); yeniden sıralama bir zenginleştirme, zorunluluk değil.

**Alternatifler:**
- *Aday sayısı eşiğine göre otomatik karar* — bu turda reddedildi. Eşik değeri ölçüm olmadan
  keyfî olur; önce çağrı başına maliyet görülür, sonra otomatik eşik ayrı bir karar olur.
  Açık anahtar şimdi, otomatik eşik sonra — ikisi çakışmaz.
- *Ortam değişkeniyle global kapatma* — tek başına reddedildi. Karar istek başınadır; global
  anahtar her tüketiciyi aynı kefeye koyar.

---

## R10 — Site tarafında caching neden hemen işe yarar?

**Karar: Site'da açık breakpoint koymak tek başına yeterli; ama önce kiracı belleği prefix'in
başından çıkarılmalı.**

Motorun tersi bir durum: site'ın prefix'i zaten çok büyük (sistem promptu + dossier + büyüyen
konuşma geçmişi) ve site'ın varsayılan ajan modelinin cache eşiği motorunkinden **belirgin
biçimde düşük**. Yani içerik zaten eşiğin üstünde; eksik olan tek şey işaretleme.

Doğrulandı: `backend/src` altında tek bir `cache_control` / `ephemeral` kullanımı yok.
`packWizardPrompt.ts` ve `signalChatPrompt.ts` yorumları "cache-friendly" diyor ama breakpoint
koyan kod yok.

**Kritik önkoşul:** `promptLoader.ts:32-37` içindeki `injectTenantMemory`, kiracı gerçeklerini
sistem promptunun **en önüne** ekliyor (`signalGraph/nodes.ts:62`). Cache prefix eşleşmesi
olduğu için bu, prefix'in ilk baytlarını her kiracıda ve kiracı belleği her değiştiğinde
farklılaştırır — breakpoint konsa bile hiçbir şey kazandırmaz, hatta her turda yazma primi
ödetir. Kiracı belleği statik prefix'ten **sonraya** taşınmalıdır.

**Gerekçe:** Bu, "silent invalidator" kalıbının ders kitabı örneği: sistem promptuna
istek/kiracı bazlı içerik enjekte etmek.

**Alternatifler:**
- *Kiracı belleğini olduğu yerde bırakıp yalnızca konuşma kuyruğunu cache'lemek* — reddedildi.
  Sistem bloğu render sırasında konuşmadan önce geliyor; başı değişen prefix sonrasındaki her
  şeyi geçersiz kılar.
- *Kiracı belleğini tamamen kaldırmak* — reddedildi. İşlevsel; sadece yeri yanlış.

---

## R11 — Site'ın sihirbaz girdi sınırı ne olmalı?

**Karar: Sınır uygulanır ve mevcut kalıp örnek alınır; kesin değer implementasyonda
belirlenir.**

`tools.ts:139-143`'te `text`, `db_schema` ve `sample_data` parametrelerinde üst sınır yok.
Yalnızca `sample_data` daha sonra promptta 3.000 karaktere kırpılıyor (`packWizardPrompt.ts`)
— yani girdinin geri kalanı dossier'a olduğu gibi giriyor ve ajan turlarının **tamamında**
yeniden gönderiliyor.

Doğru kalıp aynı dosyada zaten var: signal chat'in `text` parametresi
`z.string().min(10).max(MAX_SIGNAL_TEXT_CHARS)` (`tools.ts:58, 251`) ile sınırlı. Aynı kalıp
sihirbaza uygulanır; her alan için ayrı sınır (serbest metin, DDL ve örnek satırlar farklı
büyüklük profillerine sahip).

**Gerekçe:** Maliyet tur sayısıyla çarpılıyor ve sabit tek bir başlatma ücretiyle karşılanıyor.
Sınır, girdi kabul edilmeden — yani hiçbir LLM çağrısı yapılmadan ve hiçbir kredi düşülmeden —
uygulanır.

**Alternatifler:**
- *Promptta kırpmak* — reddedildi. Sessiz kırpma; kullanıcı verisinin bir kısmının yok
  sayıldığını kimse görmez. Doğrulama katmanında açık ret, doğru cevaptır.
- *Sınırı yalnızca uyarı yapmak* — reddedildi. Uyarı maliyeti durdurmaz.

---

## R12 — Zaman aşımı ve batch dayanıklılığı

**Karar: Her sağlayıcı çağrısına açık zaman aşımı; batch kimliği kalıcı; yoklama döngüsüne üst
sınır.**

Doğrulanan durum:
- Hiçbir LLM çağrısında açık zaman aşımı yok (`base.py:144-155`, `multi_llm.py`). Yalnızca
  SDK'nın kendi varsayılanı devrede ve bu varsayılan **dakikalar** mertebesinde.
- Realtime worker sıralı çalışıyor — tek asılı çağrı tüm kuyruğu durdurur.
- `submit_and_await_batch` (`base.py:260-264`) `while True` ile yokluyor; üst sınır yok.
- `batch_worker.py:175-189` batch kimliğini hiçbir yere yazmıyor. Gönderim ile sonuçların
  yazılması arasında süreç ölürse **ödenmiş** batch tamamen kaybolur; kurtarma yalnızca bayat
  görev geri alımıyla, yani sıfırdan yeniden gönderimle mümkün.
- Anthropic dışı sağlayıcılar batch indiriminden yararlanmıyor; `batch_worker.py:200-218`
  sinyal başına senkron çağrıya düşüyor.

**Gerekçe:** Zaman aşımı küçük bir değişiklik ve doğrudan throughput etkisi var. Kayıp batch
doğrudan paradır: iş ödenmiş, sonuç alınmamış.

**Alternatifler:**
- *Yalnızca SDK varsayılanına güvenmek* — reddedildi. Varsayılan, sıralı bir worker kuyruğu
  için fazla uzun; ayrıca yapılandırılamaz olması operasyonel körlüktür.
- *Batch'i tamamen senkron yola çevirmek* — reddedildi. Batch indirimi gerçek ve backfill için
  değerli; dayanıklılık eklemek onu terk etmekten ucuz.

---

## R13 — Harcama tavanı nereye konacak?

**Karar: Mevcut tier guard kalıbı genişletilir; tavan yalnızca platform anahtarıyla çalışan
kiracılar için anlamlıdır.**

`usage_service.TIER_LIMITS` (`services/usage_service.py:22-38`) ücretli tier'ların her
boyutunda `None` (sınırsız) ve token boyutu hiç tanımlı değil. `llm_token_count` kaydediliyor
ama hiçbir guard okumuyor. `middleware/billing_guard.py:27-31` yalnızca sinyal/varlık/pack
sayılarına bakıyor.

BYOK'ta gerçek maliyeti kiracının kendi anahtarı ödüyor — orada tavanın anlamı farklıdır
(kiracıyı kendi faturasından korumak). Platform anahtarıyla çalışan kiracıda ise tavansızlık
doğrudan zarardır.

**Gerekçe:** Ölçüm (R6) tavanı beslemenin ön koşulu; token muhasebesi doğru olmadan tavan
yanlış yerde tetiklenir. Bu yüzden bu iş ölçümün arkasında sıralanır.

**Alternatifler:**
- *Yeni bir guard middleware'i* — reddedildi (`HM-REAS-01`). Kalıp zaten var; ikinci bir
  middleware aynı kararı iki yerde verir.

---

## R14 — Kalite regresyonu nasıl atfedilecek?

**Karar: Mevcut replay/karşılaştırma mekanizması kullanılır; kapsam bu turda genişletilmez.**

R3 metrik değerlerini değiştirebilir — bu kabul edilmiş ve kasıtlıdır (`HM-REAS-05`). Ancak
"kasıtlı değişim" ile "sessiz model sürüklenmesi" ayırt edilebilir olmalıdır.

Mekanizma zaten var: `replay.py:438-450` karşılaştırmada bir hash değişimi eşlik ediyorsa
sürüklenmeyi "muhtemelen kasıtlı prompt/bağlam güncellemesi" olarak, hash değişmemişse
"açıklanamayan model sürüklenmesi" olarak sınıflıyor. R3 `prompt_hash`'i değiştireceği için
sürüklenme doğru kutuya düşer.

Bilinen sınır: tek golden set var (`packs/canary/saha-hizmet-isci-canary.yaml`) ve depodaki 11
pack'ten yalnızca 1'ini kapsıyor; ayrıca CI'da koşmuyor. Bu turda **genişletilmiyor**; sınırın
bilinmesi ve yazılı olması yeterli (bkz. plan § Teknik borç).

**Gerekçe:** Var olan mekanizmayı kullanmak, yeni bir eval altyapısı kurmaktan hem ucuz hem
dürüst — kapsamın dar olduğunu gizlemez.

**Alternatifler:**
- *Bu turda eval harness'ı genişletmek* — kullanıcı kararıyla kapsam dışı.

---

## Çözülen belirsizlikler özeti

| Soru | Durum |
|---|---|
| Prefix cache eşiğinin altında mı? | **Çözüldü** (R1) — evet, doğrulandı. |
| Prefix bayt-özdeş mi? | **Çözüldü** (R2) — evet; iki nokta kalıcı testle kilitlenecek. |
| Pack promptu tabanı ezmeli mi? | **Çözüldü** (R3) — hayır, birleştirilecek. |
| Çıktı kesilmesi nasıl görülecek? | **Çözüldü** (R4) — durma nedeni okunacak. |
| Token nasıl sayılacak? | **Çözüldü** (R5) — yerel tahmin + çevrimdışı kalibrasyon. |
| Sağlayıcı cache alanları? | **Karara bağlandı** (R6) — Anthropic doğrulandı; diğerleri implementasyonda doğrulanır, yoksa `NULL`. |
| Yeni RLS gerekiyor mu? | **Çözüldü** (R7) — hayır; gerekçe yazılı. |
| Bütçe aşımında davranış? | **Çözüldü** (R8) — işaretli devam. |
| Yeniden sıralama nasıl kapanır? | **Çözüldü** (R9) — istek başına anahtar, varsayılan bugünkü davranış. |
| Site caching neden hemen çalışır? | **Çözüldü** (R10) — eşik düşük, prefix büyük; önce kiracı belleği taşınır. |
| Sihirbaz sınırı ne olmalı? | **Karara bağlandı** (R11) — mevcut kalıp uygulanır, değer implementasyonda. |
| Zaman aşımı / batch? | **Çözüldü** (R12). |
| Tavan nereye? | **Çözüldü** (R13) — mevcut guard genişletilir. |
| Regresyon nasıl atfedilir? | **Çözüldü** (R14) — mevcut replay karşılaştırması. |

**Açık NEEDS CLARIFICATION kalmadı.** R6 ve R11'deki "implementasyonda doğrulanacak" maddeleri
belirsizlik değil, bilinçli olarak uygulama anına bırakılmış doğrulama adımlarıdır ve plandaki
görevlerde açık kabul kriteri taşırlar.
