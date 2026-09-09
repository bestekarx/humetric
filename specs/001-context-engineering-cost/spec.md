# Feature Specification: Bağlam Mühendisliği ve Maliyet Optimizasyonu

**Feature Branch**: `001-context-engineering-cost`

**Created**: 2026-09-07

**Status**: Draft

**Input**: Kullanıcının keşif notu — "Prompt boyutu hiçbir yerde ölçülmüyor, prompt cache
sessizce hiç devreye girmiyor ve bunu kimse göremiyor." Kapsam: humetric motoru +
`../humetric-site` MCP ajanları.

## Neden

HuMetric AI-native bir üründür: değerin tamamı LLM çağrısının **ne gördüğüne** ve **ne kadara
gördüğüne** bağlıdır. Bugün bu iki şeyin hiçbiri ölçülmüyor. Keşifte doğrulanan üç somut durum:

1. **Bağlam ölçümsüz.** Token sayımı, bütçe ya da chunking yok. `SignalCreate.text` 300.000
   karaktere kadar kabul ediliyor (`src/humetric/schema.py:253`) ve extractor'ın user mesajına
   aynen giriyor (`src/humetric/agents/extractor.py:56-74`) — her denemede, her retry'da ve her
   replay koşusunda yeniden.
2. **Prompt cache sessizce çalışmıyor.** `cache_control` doğru yere konmuş
   (`src/humetric/agents/base.py:115-119` — sistem bloğu + tool şeması), ama cache'lenen prefix
   ölçülü olarak ~1.000 token: sistem promptu 1.357 karakter (`prompts/extractor-default.md`),
   tool şeması, artı pack'in allowed-keys bloğu. Yapılandırılmış sağlayıcının varsayılan agent
   modeli için minimum cache'lenebilir prefix **4.096 token**. Minimumun altındaki prefix hata
   vermez — sessizce cache'lenmez.
3. **Ve bu görünmüyor.** `base.py:167` yalnızca `input_tokens + output_tokens` topluyor;
   `cache_read_input_tokens` / `cache_creation_input_tokens` hiç okunmuyor. Hem tasarruf kaçıyor
   hem maliyet muhasebesi yanlış çıkıyor.

> **2026-09-08 ölçüm notu.** (2) yalnızca **işaretleme gerektiren** sağlayıcı yolu için
> geçerlidir. Yerel olarak ölçülen ikinci bir sağlayıcıda cache **otomatiktir** ve 762 token'lık
> bir prefix'te ikinci çağrının 640 token'ı cache'ten okundu — yani orada cache bugün zaten
> çalışıyor, görünmüyor olmasının tek nedeni (3). Bu, (1) ve (3)'ü değiştirmez: ölçüm her
> sağlayıcıda eksiktir.

(1) ve (2) aynı düzeltmeyle çözülür: değişmez kuralları, rubric'i ve few-shot çapaları
cache'lenen sistem bloğuna taşımak hem çıkarım kalitesini yükseltir hem prefix'i cache
minimumunun üstüne çıkarır.

Site tarafı aynı hatanın aynadaki hâli ve sistemdeki **en büyük tek maliyet kalemi orada**:
`humetric_pack_wizard_start`'ın `text` / `db_schema` / `sample_data` parametrelerinde `.max()`
yok (`backend/src/mcpServer/tools.ts:139-143`) ve bu içerik ajan turlarının tamamında yeniden
gönderiliyor, üstelik hiç caching olmadan. İki repo için tek bir bağlam sözleşmesi yazmanın
nedeni bu: aynı hata iki yerde bağımsız olarak tekrarlanmış.

## Clarifications

### Session 2026-09-08

- Q: Cache kanıtı (SC-003) nerede koşacak — CI sahte anahtarla çalışıyor ve `tests/` gitignore'da? → A: Ayrıştırılır — CI'da anahtar gerektirmeyen prefix stabilite/eşik kontrolü, `cache_read_tokens > 0` assert'i ise gerçek anahtarla yerel manuel doğrulama.
- Q: Harcama tavanı (FR-015) aşıldığında ne olur? → A: Platform anahtarıyla çalışan kiracıda istek `429` ile reddedilir (mevcut `billing_guard` kalıbı); BYOK kiracılar tavansız kalır.
- Q: Sihirbaz girdi sınırları (FR-016) somut olarak kaç? → A: `text` 60.000, `db_schema` 40.000, `sample_data` 20.000 karakter.
- Q: Eşik altı cache prefix'i (FR-011) nasıl tespit edilir? → A: Prefix açılışta ölçülür; yapılandırılmış eşiğin altındaysa `WARNING` loglanır.
- Q: Extraction girdi token bütçesi (FR-005) somut olarak kaç? → A: 32.000 token; A1 ölçümünden sonra ayarlanabilir, ayarlanana kadar bağlayıcı.
- Q: Cache eşiği (FR-011) nereden okunur — model tablosu mu, sağlayıcı başına mı? → A: `config.py`'de tek bir ortam değişkeni; varsayılanı bilinen en muhafazakâr (en yüksek) eşik, dağıtım kendi modeline göre düşürür.
- Q: SC-002/SC-010'un dayandığı "değişiklik öncesi" ölçüm nasıl garanti edilir? → A: Yeni FR-020 — hiçbir kod değişikliği landlamadan önce metrik değerleri ve maliyet baseline'ı yakalanır; A1 bu adım bitmeden başlamaz.
- Q: `/v1/query`'ye eklenecek yeniden sıralama anahtarının adı ne? → A: `rerank` (bool, varsayılan `true`); tek kelime olduğu için ayrı camelCase alias gerekmez.
- Q: Tahmin sapması (FR-004) neye karşı ölçülür — sağlayıcının sayım ucuna mı? → A: Hayır; kaydedilmiş gerçek veriye karşı — `trace_data.measured_input_tokens` ile `llm_call_record.input_tokens` karşılaştırılır. Ağ çağrısı yok, dört sağlayıcıda da çalışır.

### Session 2026-09-08 (ikinci tur — yerel sağlayıcı ölçümü)

- Q: Yerel doğrulama hangi sağlayıcıyla koşacak? → A: DeepSeek. Yerel kiracı 1 zaten
  `llm_provider = deepseek` ve anahtarı `.env`'de. Anthropic yolu ayrıca, kendi anahtarıyla
  doğrulanır; ikisi aynı kanıt değildir (bkz. bir sonraki madde).
- Q: DeepSeek'te cache nasıl davranıyor? → A: **Ölçüldü, varsayılmadı.** Cache *otomatik* —
  istekte hiçbir işaretleme (`cache_control`) yok ve ikinci çağrı cache'ten okuyor. Ölçüm:
  762 token'lık bir prefix'te birinci çağrı `cache_hit=0 / cache_miss=762`, ikinci çağrı
  `cache_hit=640 / cache_miss=122` (64 token'lık blok granülaritesi). Alan adları
  `usage.prompt_cache_hit_tokens` / `usage.prompt_cache_miss_tokens`
  (ayrıca `usage.prompt_tokens_details.cached_tokens`); **cache yazma sayısı raporlanmıyor**,
  `cache_write_tokens` bu sağlayıcıda `NULL` kalır.
- Q: Google/Gemini yolunda cache nasıl davranıyor? → A: **Ölçüldü (2026-09-09).** Örtük cache
  var ama eşikli **ve deterministik değil**: 736 ve 1.339 token'lık prefix'lerde hiç devreye
  girmedi; 2.529 ve 4.209 token'lık prefix'lerde ikinci çağrıda devreye girdi
  (`cached_content_token_count` = 2.026 ve 4.069) ve **üçüncü çağrıda ikisinde de sıfıra düştü**.
  Sonuç: SC-003'ün "ikinci çağrıda > 0" kriteri bu sağlayıcıda kararsızdır; kanıt oran
  üzerinden kurulur. Ayrıca `cached_content_token_count` alanı her yanıtta mevcuttur ve
  isabet yokken `0` döner — bu `0` gerçek bir ölçümdür, `NULL`'a çevrilmez (FR-002 netleşmesi).
- Q: O hâlde "prefix eşiğin altında, cache sessizce çalışmıyor" teşhisi ne oluyor? → A: Teşhis
  **sağlayıcıya özgüdür.** İşaretleme gerektiren ve yüksek minimum prefix eşiği olan sağlayıcıda
  (bugün yalnızca Anthropic yolunda `cache_control` var) geçerlidir; otomatik cache'leyen ve
  düşük granülariteli bir sağlayıcıda cache bugün zaten çalışmaktadır. A3'ün prompt kompozisyonu
  düzeltmesi **kalite gerekçesiyle** yine yapılır — cache yalnızca ikinci faydasıdır.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Bir çağrının gerçekte ne kadara mal olduğunu görebilmek (Priority: P1)

Platform sahibi olarak, işlenmiş bir sinyalin extraction çağrısının kaç input token'ı
harcadığını, kaçının cache'ten okunduğunu ve kaç output token ürettiğini veritabanından
sorgulayabilmek istiyorum — çünkü ölçüm olmadan sonraki hiçbir iyileştirmenin işe yaradığı
kanıtlanamaz.

**Why this priority**: Körlüğü kaldırır ve **üretilen metrik değerlerini değiştirmez**; tek
başına güvenle dağıtılabilir. (İz kaydına yeni bir ölçüm alanı eklenir; metrik değerleri
birebir aynı kalır.) Diğer bütün hikâyelerin doğrulaması buna dayanır.

**Independent Test**: Bir sinyal ingest edilir, worker işler, `llm_call_record` sorgulanır;
input/output ayrımı ve cache token alanları dolu gelir. Metrik değerlerinin bu hikâyeden önce
ve sonra aynı kaldığı görülür.

**Acceptance Scenarios**:

1. **Given** çalışan bir stack ve bir metric pack, **When** bir sinyal ingest edilip
   tamamlandığında, **Then** o çağrının satırında input token, output token ve cache token
   alanları ayrı ayrı okunabilir.
2. **Given** sağlayıcının yanıtı cache alanı taşımıyor, **When** çağrı kaydedilir, **Then**
   ilgili alanlar `NULL` kalır — sıfır ya da uydurma bir değer yazılmaz.
3. **Given** bu hikâye dağıtıldı, **When** aynı sinyal yeniden işlenir, **Then** üretilen metrik
   değerleri hikâye öncesiyle birebir aynıdır.

---

### User Story 2 — Sınırsız sihirbaz girdisinin faturayı katlamasını durdurmak (Priority: P1)

Platform sahibi olarak, Pack Wizard oturumunu başlatan serbest metin ve şema girdilerinin bir
üst sınırı olmasını istiyorum — çünkü bugün sınırsız bir girdi ajanın her turunda yeniden
gönderiliyor ve sabit ücretle karşılanıyor.

**Why this priority**: Sistemdeki en büyük tek maliyet kalemi ve en küçük değişiklik. Doğru
kalıp zaten repoda var (signal chat `text` alanı sınırlı), sadece sihirbaza uygulanmamış.

**Independent Test**: Sihirbaz başlatma aracına sınırı aşan bir girdi gönderilir; şema
doğrulaması reddeder ve ajan turu hiç başlamaz.

**Acceptance Scenarios**:

1. **Given** 60.000 karakteri aşan bir `text` ya da 40.000 karakteri aşan bir `db_schema`,
   **When** sihirbaz başlatılır, **Then** istek doğrulama hatasıyla reddedilir ve hiçbir LLM
   çağrısı yapılmaz, hiçbir kredi düşülmez.
2. **Given** sınır içinde bir girdi, **When** sihirbaz başlatılır, **Then** oturum bugünkü gibi
   normal başlar.

---

### User Story 3 — Değişmez çıkarım kurallarının pack override'ında kaybolmaması (Priority: P2)

Pack yazarı olarak, kendi alan çerçevemi (domain framing) verirken ölçeğin, `source_span`
zorunluluğunun ve kalibrasyon kurallarının yürürlükte kalmasını istiyorum — çünkü bugün pack
promptu tanımlıysa taban prompt **tamamen** atılıyor.

**Why this priority**: Doğrudan çıkarım kalitesi sorunu; ayrıca cache'lenen bloğu büyüterek
Story 4'ün önkoşulunu yaratır. Metrik değerlerini değiştirebildiği için ölçüm (Story 1)
olmadan yapılmamalıdır.

**Independent Test**: Pack promptu tanımlı bir pack ile bir sinyal işlenir; üretilen sistem
promptunun hem değişmez bloğu hem pack'in çerçevesini içerdiği doğrulanır.

**Acceptance Scenarios**:

1. **Given** `prompts.extraction` tanımlı bir pack, **When** extraction çağrısı kurulur,
   **Then** değişmez blok (ölçek, `source_span`, kalibrasyon, doğru/yanlış çıktı örneği) yine
   de sistem promptunda bulunur.
2. **Given** bir pack YAML'ında tanınmayan bir prompt anahtarı, **When** pack kaydedilir,
   **Then** sessizce düşürülmek yerine açık bir doğrulama hatası döner.
3. **Given** modelin çıktısı token sınırına takıldı, **When** yanıt işlenir, **Then** sessizce
   boş metrik listesi dönmek yerine açık bir hata üretilir.

---

### User Story 4 — Cache'in gerçekten çalıştığını kanıtlamak (Priority: P2)

Platform sahibi olarak, arka arkaya gelen iki çağrının ikincisinde prefix'in gerçekten
cache'ten okunduğunu görmek istiyorum — çünkü cache regresyonu sessizdir: istek başarılı olmaya
devam eder, sadece fatura büyür.

**Why this priority**: Planın merkezî iddiasının testi. Story 1 ve 3 olmadan ne ölçülebilir ne
mümkündür.

**Independent Test**: Aynı pack'le iki sinyal arka arkaya işlenir; ikinci çağrının cache okuma
token'ı sıfırdan büyüktür.

**Acceptance Scenarios**:

1. **Given** aynı pack ve aynı değişmez prefix, **When** ikinci çağrı yapılır, **Then** cache
   okuma token sayısı sıfırdan büyüktür.
2. **Given** yapılandırılmış modelin minimum cache eşiği prefix'ten büyük, **When** uygulama
   açılışta prefix'i ölçer, **Then** bir `WARNING` loglanır ve sistem ya prefix'i bilinçli
   olarak eşiğin üstüne çıkarır ya da o model için cache işaretlemesini kapatır — sessiz
   kalmaz.
3. **Given** iki özdeş çağrı, **When** prefix baytları karşılaştırılır, **Then** aralarında fark
   yoktur (zaman damgası, UUID, sırasız serileştirme bulunmaz).

---

### User Story 5 — Bütçeyi aşan bağlamın sessizce geçmemesi (Priority: P3)

Operatör olarak, bir sinyalin bağlamı bütçeyi aştığında bunun kayda geçmesini istiyorum —
sonucun sessizce kırpılmasını ya da işin tamamen düşmesini değil.

**Why this priority**: Ölçüm (Story 1) gerçek dağılımı gösterdikten sonra anlamlıdır; eşik
körlemesine seçilirse ya hiç tetiklenmez ya her şeyi işaretler.

**Independent Test**: Bütçeyi aşan bir sinyal ingest edilir; çağrı yapılır, üretilen metrikler
incelemeye işaretlenir ve iz kaydında ölçülen token sayısıyla birlikte aşım notu bulunur.

**Acceptance Scenarios**:

1. **Given** bütçeyi aşan bir sinyal, **When** işlenir, **Then** çağrı yapılır, metrikler
   inceleme bekler durumuna alınır ve iz kaydına ölçülen token sayısıyla aşım yazılır.
2. **Given** bütçe içinde bir sinyal, **When** işlenir, **Then** hiçbir işaretleme eklenmez.
3. **Given** herhangi bir sinyal, **When** bütçe aşılır, **Then** metin **kırpılmaz**.

---

### User Story 6 — Ucuz sorguların LLM'e hiç gitmemesi (Priority: P3)

API tüketicisi olarak, LLM yeniden sıralamasına ihtiyaç duymayan bir sorguda bu maliyeti
ödememek istiyorum — bugün her sorgu koşulsuz olarak sıralayıcıyı çalıştırıyor.

**Why this priority**: Net tasarruf, küçük yüzey; ancak yanıt şeklini etkilediği için ölçüm
sonrasına bırakılır.

**Independent Test**: Yeniden sıralama kapalı bir sorgu gönderilir; sonuç döner ve o istek için
hiçbir LLM çağrı kaydı oluşmaz.

**Acceptance Scenarios**:

1. **Given** yeniden sıralamanın kapatıldığı bir sorgu, **When** çalıştırılır, **Then** sonuçlar
   hibrit aramanın kendi skoruyla döner ve LLM çağrısı yapılmaz.
2. **Given** parametre verilmemiş bir sorgu, **When** çalıştırılır, **Then** bugünkü davranış
   korunur (geriye dönük uyumluluk).

---

### User Story 7 — Ödenmiş bir batch'in, asılı bir çağrının ve sınırsız harcamanın işi kilitlememesi (Priority: P3)

Operatör olarak, gönderilmiş bir batch işinin süreç çökmesinde kaybolmamasını, tek bir asılı
LLM çağrısının tüm kuyruğu durdurmamasını ve platform anahtarıyla çalışan bir kiracının
sınırsız harcama yapamamasını istiyorum.

**Why this priority**: Doğrudan para ve throughput; ancak ölçüm ve cache işi bittikten sonra
ele alınır.

**Independent Test**: Batch gönderimi sonrası süreç yeniden başlatılır; iş sıfırdan yeniden
gönderilmek yerine kaldığı yerden devam eder. Ayrıca yanıt vermeyen bir sağlayıcı çağrısı
sınırlı sürede sonlanır.

**Acceptance Scenarios**:

1. **Given** gönderilmiş bir batch, **When** süreç yazma öncesinde çöker, **Then** yeniden
   başlatmada aynı batch kimliği bulunur ve sonuçlar yeniden ödeme yapılmadan toplanır.
2. **Given** yanıt vermeyen bir sağlayıcı, **When** bir LLM çağrısı yapılır, **Then** çağrı
   yapılandırılmış süre sonunda hata ile biter ve worker sıradaki işe geçer.
3. **Given** hiç bitmeyen bir batch, **When** yoklama döngüsü çalışır, **Then** döngü sonsuza
   kadar dönmez; üst sınıra ulaşınca hata verir.
4. **Given** platform anahtarıyla çalışan ve günlük token tavanını aşmış bir kiracı, **When**
   yeni bir istek gelir, **Then** istek `429` ile reddedilir ve hiçbir LLM çağrısı yapılmaz.
5. **Given** kendi anahtarını getiren (BYOK) bir kiracı, **When** aynı hacim işlenir, **Then**
   tavan uygulanmaz — maliyet kiracının kendi faturasına gider.

---

### Edge Cases

- Sağlayıcının yanıtı cache token alanı taşımıyorsa? → Alan `NULL` bırakılır; sıfır yazmak
  "cache çalışmadı" ile "sağlayıcı raporlamıyor"u ayırt edilemez hâle getirir.
- Sinyal metni boşsa ya da yalnızca yapılandırılmış veri varsa? → Ölçüm sıfır döner, bütçe
  kapısı tetiklenmez, mevcut davranış korunur.
- Pack promptu tanımlı **ve** pack'te hiç metrik yoksa? → Değişmez blok yine render edilir;
  allowed-keys bloğu boş kalır.
- Aynı sinyal iki worker tarafından aynı anda alınırsa? → Kuyruk `SKIP LOCKED` ile korunuyor;
  yeni yazılan token alanları çağrı başına eklenir, güncellenmez — tekrar oynatma çift satır
  üretir, bu kasıtlıdır (çağrı gerçekten iki kez yapılmıştır).
- Yeniden sıralama kapalıyken hibrit arama hiç aday döndürmezse? → Boş sonuç listesi döner,
  hata değil.
- Bütçe aşımı **ve** rıza reddi aynı metrikte çakışırsa? → Rıza kapısı kazanır; metrik hiç
  yazılmaz, bu yüzden işaretlenecek bir şey de kalmaz.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sistem, bir extraction çağrısının input, output ve cache token sayılarını ayrı
  ayrı kalıcı olarak kaydetmek ZORUNDADIR.
- **FR-002**: Sistem, sağlayıcının raporlamadığı bir token türünü `NULL` olarak bırakmak
  ZORUNDADIR; tahmin ya da sıfır yazamaz.
- **FR-003**: Sistem, extraction bağlamının token büyüklüğünü çağrı **öncesinde**, sağlayıcıya
  ek bir ağ isteği yapmadan tahmin edebilmek ZORUNDADIR.
- **FR-004**: Sistem, tahmincinin gerçek sayımdan sapmasını çevrimdışı olarak ölçebilen bir araç
  sağlamak ZORUNDADIR. Sapma, sağlayıcının sayım ucuna değil **kaydedilmiş gerçek veriye**
  karşı ölçülür: tahmin `trace_data.measured_input_tokens`, gerçek değer
  `llm_call_record.input_tokens`. Böylece araç hiçbir ağ çağrısı yapmaz ve sayım ucu
  olmayan sağlayıcılarda da çalışır; karşılığında FR-001 ve FR-003'ün dağıtılmış olmasını
  gerektirir.
- **FR-005**: Sistem, yapılandırılabilir bir extraction girdi token bütçesi tanımak ZORUNDADIR.
  Başlangıç değeri **32.000 token**'dır. Bu değer A1'in ölçümü gerçek dağılımı gösterdikten
  sonra ayarlanabilir; ayarlanana kadar bağlayıcıdır (FR-016'nın sihirbaz sınırlarıyla aynı
  kural).
- **FR-006**: Bütçe aşıldığında sistem çağrıyı yapmak, üretilen metrikleri incelemeye
  işaretlemek ve iz kaydına ölçülen token sayısıyla birlikte aşımı yazmak ZORUNDADIR. Sistem
  metni kırpamaz ve çağrıyı sessizce düşüremez.
- **FR-007**: Sistem, pack'e özgü prompt tanımlıyken bile değişmez çıkarım bloğunu (ölçek,
  `source_span` zorunluluğu, kalibrasyon kuralları, çıktı biçimi örneği) sistem promptunda
  render etmek ZORUNDADIR; pack yalnızca alan çerçevesi ekler.
- **FR-008**: Sistem, pack prompt tanımında tanınmayan anahtarları sessizce düşürmek yerine
  reddetmek ZORUNDADIR.
- **FR-009**: Sistem, model çıktısı token sınırına takıldığında açık bir hata üretmek
  ZORUNDADIR; boş metrik listesi dönemez.
- **FR-010**: Sistem, cache'lenen prefix'i çağrıdan çağrıya bayt-özdeş üretmek ZORUNDADIR
  (deterministik sıralama, zaman damgası/UUID yok).
- **FR-011**: Sistem, cache'lenen prefix'i açılışta ölçmek ve yapılandırılmış minimum eşiğin
  altında kaldığında `WARNING` seviyesinde loglamak ZORUNDADIR — sessiz kalamaz. Bu
  kontrol tek seferlik bir denetim değil, her açılışta çalışan bir kontroldür; model ya da
  prompt sonradan değiştiğinde durum yeniden görünür olur. Eşik, `config.py`'de **tek bir
  ortam değişkeni** olarak tutulur; varsayılanı bilinen en muhafazakâr (en yüksek) değerdir ve
  dağıtım kendi modeline göre düşürür. Sağlayıcı başına ya da model tablosu biçiminde
  tutulmaz — eşikler modele göre değişip monoton olmadığı için tablo sessizce bayatlar.
  Muhafazakâr varsayılan, eşiğin altında cache'leyebilen bir sağlayıcıda **yanlış pozitif**
  `WARNING` üretir (ölçüm: otomatik cache'leyen sağlayıcı 762 token'lık prefix'te cache'ledi);
  bu kabul edilebilir, çünkü uyarı tavsiyedir ve dağıtım eşiği kendi modeline göre düşürür.
  Uyarı hiçbir zaman çağrıyı engellemez.
- **FR-012**: Sistem, API tüketicisinin LLM yeniden sıralamasını istek başına kapatmasına izin
  vermek ZORUNDADIR; parametre verilmediğinde bugünkü davranış korunur. Alan adı
  `QueryRequest.rerank`'tır (bool, varsayılan `true`); tek kelime olduğu için ayrı bir
  camelCase alias tanımlanmaz.
- **FR-013**: Sistem, her sağlayıcı LLM çağrısına açık bir zaman aşımı uygulamak ZORUNDADIR.
- **FR-014**: Sistem, gönderilmiş bir batch işinin kimliğini süreç çökmesinden sağ çıkacak
  şekilde kalıcı kılmak ZORUNDADIR ve yoklama döngüsü sınırsız dönemez.
- **FR-015**: Sistem, platform anahtarıyla çalışan bir kiracı için yapılandırılabilir bir
  **günlük token tavanı** uygulamak ZORUNDADIR. Tavan aşıldığında istek `429` ile reddedilir
  ve hiçbir LLM çağrısı yapılmaz; mekanizma `middleware/billing_guard.py`'nin mevcut
  `tier_limit_exceeded` kalıbını genişletir, yeni bir mekanizma icat etmez. Kendi anahtarını
  getiren (BYOK) kiracılara tavan uygulanmaz.
- **FR-016**: Site'ın sihirbaz başlatma aracı, serbest metin ve şema girdilerine üst sınır
  uygulamak ZORUNDADIR: `text` 60.000, `db_schema` 40.000, `sample_data` 20.000 karakter.
  Sınır doğrulama katmanında uygulanır; aşıldığında hiçbir LLM çağrısı yapılmaz ve kredi
  düşülmez. Bu değerler ölçüm sonrası ayarlanabilir; ayarlanana kadar sözleşmedeki sayılar
  bağlayıcıdır.
- **FR-017**: Site, çok turlu ajan konuşmalarında statik sistem prefix'ini cache'lenebilir
  kılmak ZORUNDADIR; kiracıya özgü dinamik içerik cache'li prefix'in **önüne** yerleştirilemez.
- **FR-018**: Site, cache token'larını girdi token'larından ayrı kaydetmek ZORUNDADIR.
- **FR-019**: Her iki depo için ortak bağlam sözleşmesi belgelenmek ZORUNDADIR: bütçe, prompt
  kompozisyonu, cache katmanları, maliyet muhasebesi.
- **FR-021**: Cache işaretlemesi (`cache_control`) **yalnızca onu gerektiren sağlayıcı dalında**
  uygulanmak ZORUNDADIR. Cache'i otomatik yapan bir sağlayıcıya işaretleme eklenemez — istekte
  karşılığı yoktur ve eklemek ya etkisizdir ya hatadır. Bir sağlayıcının cache semantiği
  koda girmeden **önce ölçülerek** doğrulanır; varsayım yazılamaz (İlke II).
- **FR-020**: Bu özelliğin hiçbir kod değişikliği dağıtılmadan **önce**, karşılaştırma
  baseline'ı yakalanmak ZORUNDADIR: (a) bir örnek küme için üretilen metrik değerleri ve
  (b) mevcut maliyet ölçüm aracının sinyal başına token çıktısı. Ölçülen sayılar
  `docs/architecture/context-engineering.md`'ye yazılır; ham koşu çıktıları yereldedir.
  Ölçüm fazı (A1) bu adım tamamlanmadan başlayamaz — baseline değişiklik landladıktan sonra
  geri alınamaz biçimde kaybolur ve SC-002 ile SC-010 kanıtlanamaz hale gelir.

### Constitutional Requirements

- **CR-001**: Bu özelliğin getirdiği her okuma ve yazma yolu kiracı kapsamlı oturum altında
  çalışmak ZORUNDADIR. Bu turda yeni `tenant_id` tablosu **oluşturulmuyor**; mevcut
  `llm_call_record` tablosuna kolon eklenmesi RLS politikasını değiştirmez, ancak yazma yolunun
  (`services/usage_service.py`) mevcut kiracı bağlamı davranışı korunmak ZORUNDADIR (İlke I).
- **CR-002**: Bu özellik yeni kişisel veri alanı getirmez. Bütçe aşımı işaretlemesi mevcut
  inceleme kalıbını kullanır; hassas metrikler rıza kapısından geçmeye devam eder ve iz kaydına
  yazılan aşım notu metrik değerini ya da sinyal metnini **taşıyamaz** (İlke III).
- **CR-003**: LLM davranışı yalnızca "yapılandırılmış sağlayıcı" olarak ifade edilmek
  ZORUNDADIR; marka model adı yalnızca `config.py` içindeki ortam değişkeni varsayılanı olarak
  bulunabilir. Yeni bir LLM ajanı eklenmiyor; cache ve zaman aşımı soyutlamaları sağlayıcı
  dağıtıcısının kendi dalları içinde kalır (İlke II).
- **CR-004**: Sağlayıcı uç noktaları, sunucu adresleri ve anahtarlar izlenen hiçbir dosyaya
  giremez; yeni ayarların tamamı ortam değişkeni varsayılanı olarak tanımlanır (İlke IV).
- **CR-005**: Token kolonlarını ekleyen migration'ın `downgrade()` fonksiyonu tam olarak kendi
  `upgrade()` fonksiyonunun eklediği kolonları düşürmek ZORUNDADIR (İlke V).
- **CR-006**: Site tarafındaki değişikliklerin hiçbiri bu depoya dosya ekleyemez; sihirbaz
  girdi sınırı, çok turlu caching ve site token muhasebesi `../humetric-site` projesine aittir
  (İlke VI).

### Key Entities

- **LLM çağrı kaydı**: Bir LLM çağrısının kiracı, sinyal, pack, sağlayıcı ve model boyutlarıyla
  token muhasebesi. Bugün tek bir toplam token sayısı taşıyor; bu özellik input, output ve cache
  token türlerini ayırıyor.
- **Bağlam ölçümü**: Bir extraction çağrısına girecek sistem ve kullanıcı metinlerinin çağrı
  öncesi token tahmini. Kalıcı bir varlık değil; bütçe kapısının girdisi ve iz kaydının içeriği.
- **Bütçe aşımı işareti**: Metriğin mevcut iz kaydına eklenen, ölçülen token sayısını taşıyan
  not ve mevcut inceleme durumu.
- **Pack prompt tanımı**: Bugün tek alanlı; alan çerçevesinin yanına rubric ve örnek alanları
  eklenir ve tanınmayan anahtar reddedilir.
- **Site sihirbaz oturumu**: Serbest metin, şema ve örnek veri girdileriyle başlayan çok turlu
  ajan oturumu; girdiler her turda yeniden gönderilir.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: İşlenen bir sinyalin extraction çağrısı için input, output ve cache token
  sayıları veritabanından tek sorguyla okunabilir (bugün: hiçbiri okunamıyor).
- **SC-002**: Ölçüm fazı dağıtıldıktan sonra üretilen metrik değerleri, **FR-020 ile yakalanan
  baseline'daki** değerlerle birebir aynıdır — bu fazın davranış değişikliği sıfırdır.
- **SC-003**: Aynı pack'le arka arkaya işlenen iki sinyalin ikincisinde cache okuma token sayısı
  sıfırdan büyüktür. Doğrulama iki parçadır: (a) CI'da anahtar gerektirmeyen prefix
  bayt-özdeşlik ve eşik kontrolü, (b) gerçek sağlayıcı anahtarıyla yerel manuel doğrulama.
  **(b) sağlayıcıya göre iki ayrı kanıttır:** otomatik cache'leyen sağlayıcıda kanıt, ölçümün
  `llm_call_record`'a doğru yazıldığını gösterir (cache zaten çalışıyordu — "bugün her zaman
  sıfır" o yol için doğru değildi, kayıt körlüğü bunu gizliyordu); işaretleme gerektiren
  sağlayıcıda kanıt, prefix'in eşiği geçmesiyle cache'in **ilk kez** devreye girdiğini gösterir.
  İkisi birbirinin yerine geçmez. **Örtük cache'i deterministik olmayan sağlayıcıda** (ölçüm:
  Google) kriter tek çağrı çifti değil, birden çok çağrıdaki **cache okuma oranıdır** — tek bir
  `> 0` assert'i orada kararsızdır.
- **SC-004**: Cache'lenen prefix, yapılandırılmış minimum eşiği aşar; aşmıyorsa açılışta
  `WARNING` loglanır ve cache işaretlemesi bilinçli olarak kapatılmıştır. Otomatik cache'leyen
  bir sağlayıcıda bu uyarı yanlış pozitif olabilir (FR-011) ve dağıtımın eşiği düşürmesiyle
  giderilir — kod değişikliğiyle değil.
- **SC-005**: Bütçeyi aşan bir sinyalde metin kırpılmaz, çağrı düşmez ve aşım hem inceleme
  durumundan hem iz kaydından görülebilir.
- **SC-006**: Pack promptu tanımlı bir pack'le yapılan çağrıda değişmez kural bloğu sistem
  promptunda bulunur (bugün: tamamen düşüyor).
- **SC-007**: Sihirbaz başlatma aracı 60.000 karakterden uzun bir `text`'i (ya da 40.000'den
  uzun bir `db_schema`'yı) reddeder ve bu istekte hiçbir LLM çağrısı yapılmaz, kredi düşülmez.
- **SC-008**: Site'da bir ajan oturumunun ikinci turunda cache okuma token sayısı sıfırdan
  büyüktür ve bu değer girdi token'ından ayrı kaydedilmiştir.
- **SC-009**: Yanıt vermeyen bir sağlayıcı çağrısı yapılandırılmış süre içinde sonlanır; worker
  kuyruğu bir çağrı yüzünden durmaz.
- **SC-010**: Sinyal başına ortalama token maliyeti, mevcut maliyet ölçüm aracıyla önce/sonra
  karşılaştırıldığında ölçülebilir biçimde raporlanır; "önce" değeri FR-020'nin baseline'ıdır.
- **SC-011**: Günlük token tavanını aşmış, platform anahtarıyla çalışan bir kiracının isteği
  `429` alır ve o istekte `llm_call_record`'a hiçbir satır eklenmez; BYOK kiracının aynı
  isteği normal işlenir.

## Assumptions

- **Ölçüm önce gelir.** Uygulama sırası kullanıcı tarafından kararlaştırılmıştır:
  ölçüm → sihirbaz girdi sınırı → prompt kompozisyonu + cache kanıtı → bütçe kapısı →
  gereksiz çağrıların elenmesi → batch/tavan/zaman aşımı → site caching → site ölçümü.
- **`SignalCreate.text`'in 300.000 karakter sınırı bu turda değişmiyor.** Düşürmek mevcut API
  tüketicileri için kırıcı olur; gerçek dağılım ölçüldükten sonra ayrıca değerlendirilir.
- **Uzun sinyal için chunking bu turda yapılmıyor.** Chunk sonuçlarını mevcut birleştirme
  fonksiyonuyla toplamak yanlış olur — o fonksiyon güven ağırlıklı ortalama alıyor ve kanıt
  sayacını artırıyor, yani tek sinyalin parçaları birden fazla bağımsız kanıt gibi sayılır.
  Ayrı birleştirme semantiği ister; ayrı iş.
- **Token tahmini yerel ve deterministiktir.** Sinyal başına sağlayıcının sayım ucuna ağ çağrısı
  eklenmez (gecikme + hata yüzeyi); sapma çevrimdışı kalibre edilir.
- **Cache eşikleri modele göre değişir ve monoton değildir.** Motorun varsayılan agent modeli
  ile site'ın varsayılan ajan modeli farklı eşiklerdedir; bu yüzden aynı hata iki tarafta farklı
  şiddette görünür.
- **Eval/regresyon güvenliği ve orkestrasyon dayanıklılığı bu turda kapsam dışıdır.** Mevcut tek
  golden set ve CI'da koşmayan canary harness, kısmi yazma riski, dead-letter eksikliği ve
  idempotency boşluğu teknik borç olarak plana kaydedilir.
- **Site'ın Pack Wizard'ı motorun kendi sihirbazını çağırmaz ve bu ayrım korunur.** Motor pack
  YAML'ını doğrular; üreten ajan deneyimi site'a aittir.

### Kapsam sınırı (Anayasa İlke VI)

Bu depo yalnızca backend motorudur. **Bu depoda yapılacaklar:** bağlam ölçümü, token kolonları
ve migration, prompt kompozisyonu, cache doğrulaması, bütçe kapısı, yeniden sıralama anahtarı,
zaman aşımı, batch dayanıklılığı, harcama tavanı ve `docs/architecture/` altındaki sözleşme
belgesi. **`../humetric-site` projesinde yapılacaklar:** sihirbaz girdi sınırı, çok turlu
caching breakpoint'leri, kiracı belleğinin cache'li prefix'ten sonraya taşınması ve site token
muhasebesinin ayrıştırılması. Bu spec'in site maddeleri o depoda uygulanır; buraya frontend ya
da site dosyası eklenmez.
