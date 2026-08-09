# Veri Toplama Stratejisi — Erken Tüketici Sinyali PoC

> **Durum:** Taslak / örnek plan
> **Kapsam:** HuMetric motorunun FMCG marka-ürün sağlık analizi için ihtiyaç duyduğu
> yapılandırılmamış metin sinyallerinin nereden, nasıl ve hangi maliyet/risk profiliyle
> toplanacağı.
> **Hedef okur:** Teknik ekip (bölüm 2-5) + başvuru/sunum hazırlayan kişi (bölüm 1 ve 7).

---

## 1. Yönetici özeti — tek paragraf

Motor, kurumun **mevcut ve lisanslı** veri kaynaklarına bağlanır; buna ek olarak
açık e-ticaret yorumlarını **lisanslı bir veri sağlayıcı** üzerinden alır. Kendi
altyapımızda sosyal medya scraping'i yapılmaz. Bu tercih hem hukuki riski
minimize eder hem de PoC'nin veri toplama maliyetini 400 doların altında tutar.
Aylık 1 milyon sinyalde LLM işleme maliyeti 500 doların altındadır.

---

## 2. Stratejik ilke: veri toplama sorumluluğunu üstlenme

Kurumsal ölçekte (holding, çok markalı FMCG) aradığımız verinin büyük kısmı
**zaten kurumun içinde veya lisanslı olarak elinde**. Kendi başımıza scraping
yapmak, marjinal veri kazancı karşılığında orantısız hukuki ve operasyonel risk
üretir.

### Kaynak envanteri

| Kaynak | Kimde | Hukuki risk | Sinyal değeri |
|---|---|---|---|
| Tüketici hattı / çağrı merkezi transkriptleri | Kurum | Sıfır (kendi verisi) | **En yüksek** — tat, ambalaj, bozulma şikayeti birebir |
| Kendi sosyal hesaplarının yorumları | Kurum | Sıfır — Meta Graph API "owned media" resmî erişim | Çok yüksek |
| Şikayet platformu kurumsal panel | Genelde abone | Sıfır (lisanslı) | Çok yüksek, TR'ye özgü |
| Sosyal dinleme aboneliği (Brandwatch / Talkwalker / Meltwater / Sprinklr) | Genelde abone | Sıfır — lisans geçmiş arşiv + kullanım hakkı içerir | Yüksek |
| Saha / kalite raporları, bayi geri bildirimi | Kurum | Sıfır | Orta-yüksek |
| E-ticaret yorumları (Trendyol, Hepsiburada, Migros, Getir) | **Kimsede yok → tek gerçek dış veri ihtiyacı** | Orta | Yüksek — SKU bazlı, tarihli, yıldızlı |

**Sonuç:** Dış veri ihtiyacı tek bir kategoriye indirgeniyor — açık e-ticaret
yorumları. Onu da lisanslı sağlayıcıdan satın alıyoruz.

---

## 3. Kanal haritası — trafik ışığı

### 🟢 Yeşil — resmî API, doğrudan kullanılır

| Kanal | Erişim | Maliyet | Not |
|---|---|---|---|
| **YouTube Data API** | Resmî API | Ücretsiz (günlük 10.000 birim kota) | Reklam videosu altındaki yorumlar tat/ambalaj sinyali için beklenmedik derecede zengin |
| **Google Maps / Places** | Places API | Ücretli, düşük | Mağaza/perakende noktası değerlendirmeleri |
| **Google Play / App Store yorumları** | Play Console / resmî | Ücretsiz | Kurumun kendi mobil uygulaması |
| **Meta Graph API** | Resmî API, token kurumda | Ücretsiz | **Sadece kurumun kendi** sayfa/hesap yorumları. BYOK mimarisine birebir oturur |

### 🟡 Sarı — ticari sağlayıcı üzerinden, kendi kodumuzla değil

| Kanal | Yöntem | Maliyet |
|---|---|---|
| **Trendyol / Hepsiburada / n11 / Amazon TR yorumları** | Apify hazır aktörler (charge-on-success) | ~$1 / 1.000 sonuç + ~$49/ay platform |
| Aynı kanallar, kurumsal alternatif | Bright Data | ~$500/ay taban — compliance dokümantasyonu daha güçlü, kurumsal satın almada avantaj |
| **Şikayet platformu** | Önce kurumsal panel/export sorulur | Abonelik varsa sıfır |

**Neden kendi scraper'ımızı yazmıyoruz — iki gerekçe:**

1. **Operasyonel:** Bot koruması (Cloudflare, rate limit, IP rotasyonu) ile
   uğraşmak haftalar alır ve sürekli bakım ister.
2. **Hukuki (daha önemli):** Sağlayıcı kullanıldığında sorumluluk sağlayıcıya
   geçer. Biz "veri satın alan" taraf oluruz, "scraper işleten" değil.

### 🔴 Kırmızı — PoC'de dokunulmaz

| Kanal | Gerekçe |
|---|---|
| **Instagram / TikTok** (başkasının hesabı) | Platform sahipleri scraping'e karşı agresif dava açıyor ve hesap kapatıyor. Public veri scraping'i ABD'de CFAA suçu değil (*hiQ v. LinkedIn*), ama ToS ihlali → sözleşme davası + kalıcı IP ban yolu tamamen açık |
| **X / Twitter** | Şubat 2026'da pay-per-use'a geçti: okuma başına $0.005, aylık 2M okuma tavanı. Eski $200 Basic / $5.000 Pro katmanları yeni kayıtlara kapalı; mevcut Basic aboneleri 1 Haziran 2026'dan itibaren pay-per-use'a taşındı. 500k tweet = $2.500. TR FMCG'de X hacmi düşük → maliyet/fayda kötü |
| **Sözlük siteleri** | Sert bot koruması, ToS yasağı, düşük hacim |
| **Login duvarı arkasındaki her şey** | Bkz. bölüm 4.3 |

---

## 4. Hukuki çerçeve — üç ayrı katman

Bu üç katman sık karıştırılıyor; ayrı ayrı ele alınmalı.

### 4.1 KVKK / GDPR

Yorum metni + kullanıcı adı + profil bilgisi = **kişisel veri**.

**Mimari çözüm (motorda zaten mevcut):**
- Yazar kimliği hiçbir aşamada saklanmaz; ingest anında hash'lenip atılır.
- Saklanan: metin + tarih + SKU/entity referansı.
- Hassas metrik anahtarları Metric Pack'te `sensitive: true` ile işaretlenir,
  embedding vektörüne dahil edilmez, rıza olmadan okuma yollarında dönmez.

> **Sunum cümlesi:** "Veri minimizasyonu ilkesi gereği yazar kimliği hiçbir
> aşamada saklanmaz; motor yalnızca metin, zaman damgası ve varlık referansı ile
> çalışır."

### 4.2 FSEK — sui generis veritabanı hakkı

Bir platformun yorum veritabanının **"önemli bir kısmını" sistematik olarak**
çekmek ihlal sayılabilir.

**Pratik sınır:** Tek marka / sınırlı SKU seti için örnekleme bu eşiğin altında
kalır. "Platformun tüm yorumlarını indirdik" ifadesinden kaçınılır; kapsam her
zaman marka+SKU bazında tanımlanır.

### 4.3 ToS ihlali ve TCK 243

- **ToS ihlali** ceza hukuku değil, sözleşme hukukudur. Tipik yaptırım IP/hesap
  engelidir.
- **TCK 243** (bilişim sistemine hukuka aykırı erişim) **login duvarı
  arkasındaki** içerik için gündeme gelir.
- **Net çizgi:** Giriş yapmadan görünen yorum ≠ suç. Hesap açarak / giriş yaparak
  çekmek tamamen ayrı bir kategoridir. **Bu çizgi hiçbir koşulda geçilmez.**

---

## 5. Maliyet modeli

### 5.1 LLM işleme maliyeti

Güncel liste fiyatları:

| Model | Girdi ($/M token) | Çıktı ($/M token) |
|---|---|---|
| `claude-haiku-4-5` | $1.00 | $5.00 |
| `claude-sonnet-4-6` | $3.00 | $15.00 |

Üç maliyet çarpanı uygulanır:

1. **Batch API — %50 indirim.** Geçmiş veri işleme gerçek zamanlı olmak zorunda
   değil; tamamı batch'e uygun.
2. **Prompt caching — cache okuma normal girdinin ~%10'u.** Pack prompt'u (metrik
   tanımları, örnekler) cache'lenir. Minimum cache'lenebilir prefix ~1024 token.
3. **Çağrı başına batching.** Yorumu tek tek göndermek yerine çağrı başına ~20
   yorum yollanır → cache hit oranı ve token verimliliği belirgin artar.

### 5.2 PoC maliyeti (200.000 yorum, 24 ay, 1 marka ailesi, ~50 SKU)

| Aşama | Model | Maliyet |
|---|---|---|
| Extraction (~10.000 batch çağrısı) | Haiku 4.5 + batch + cache | ~$70 |
| Curation (~1.200 SKU×ay penceresi) | Sonnet 4.6 + batch | ~$18 |
| Embedding | Voyage | ~$2 |
| **LLM toplam** | | **~$90** |
| Scraping (Apify, ~200k yorum) | | ~$250 |
| **Veri toplama + işleme toplamı** | | **< $400** |

### 5.3 Production maliyeti

Aylık **1 milyon yeni yorum** → **~$450–500/ay** LLM faturası.

> **Sunum cümlesi:** "Aylık 1 milyon sinyalde LLM faturası 500 doların altında
> kalıyor — çünkü extraction'ı Haiku'ya, curation'ı Sonnet'e ayırıyoruz ve batch
> işleme ile prompt caching kullanıyoruz."

---

## 6. PoC uygulama planı — 8 hafta

| Hafta | İş |
|---|---|
| 1–2 | Kurumdan çağrı merkezi + şikayet platformu + owned social export talebi. Paralelde Apify hesabı, tek SKU için 500 yorumla pipeline doğrulaması |
| 3 | Metric Pack yazımı: tat, doku, ambalaj bütünlüğü, fiyat algısı, bulunabilirlik, tazelik/SKT. **Wizard agent bunu üretebilir — demoda gösterilecek güçlü an** |
| 4–5 | Tam tarihsel yükleme, batch pipeline çalıştırması |
| 6–8 | Backtest ve çıktı hazırlığı |

**Başarı kriteri (tek cümle):** "X varyantındaki ambalaj şikayeti, pazar payı
düşüşünden 6 hafta önce sinyal verdi."

---

## 7. Sunumda kullanılacak hazır cümleler

Aşağıdaki cümleler doğrudan başvuru metnine veya demo day sunumuna alınabilir.

### Veri kaynağı sorusuna

> "Motor, kurumun mevcut veri kaynaklarına bağlanır — çağrı merkezi kayıtları,
> kendi sosyal hesaplarının yorumları, mevcut sosyal dinleme aboneliği. Ek olarak
> açık e-ticaret yorumlarını lisanslı bir veri sağlayıcı üzerinden alırız. Kendi
> altyapımızda sosyal medya scraping'i yapmıyoruz."

### Hukuki risk sorusuna

> "Üç katmanı ayırıyoruz: KVKK tarafında yazar kimliği hiçbir aşamada
> saklanmıyor. Veritabanı hakkı tarafında kapsam her zaman marka ve SKU bazında
> sınırlı. Erişim tarafında ise giriş duvarının arkasına hiçbir koşulda
> girmiyoruz — yalnızca resmî API'ler ve lisanslı sağlayıcılar."

### Maliyet sorusuna

> "PoC'nin toplam veri toplama ve işleme maliyeti 400 doların altında. Production
> ölçeğinde, aylık 1 milyon sinyalde LLM faturası 500 doların altında kalıyor."

### Farklılaştırıcı — "bu bir sentiment dashboard'u değil"

> "Piyasada onlarca ürün duygu skoru üretiyor. Bizim motorumuzda üç şey farklı:
> zamansal sönümleme sayesinde 8 ay önceki bir şikayet bugünkü skoru bugünküyle
> aynı ağırlıkta bozmuyor; curator ajanı yeni sinyali geçmiş metrikle
> birleştiriyor, sıfırdan yeniden hesaplamıyor; ve kalibrasyon sayesinde 3 tweet
> ile 300 yorum aynı güven düzeyiyle raporlanmıyor. 'Bu skora neden güveneyim'
> sorusunun cevabı var."

### Ölçeklenebilirlik — Metric Pack

> "Yeni bir kategori veya marka için kod yazmıyoruz. Metric Pack YAML tanımıyla
> gün mertebesinde yeni metrik seti devreye alınabiliyor — aynı motor Pazarlama,
> Risk ve İK alanlarına açılabilir."

### Kurumsal güvenlik

> "Veri kurumun altyapısında kalıyor, LLM anahtarı kurumun kendi anahtarı. Satır
> düzeyi güvenlik (RLS) veritabanı seviyesinde uygulanıyor ve fail-closed
> çalışıyor: tenant bağlamı yoksa sıfır satır döner, veri sızıntısı mimari olarak
> mümkün değil."

---

## 8. Hazırlıklı olunması gereken zor sorular

| Soru | Hazır cevap |
|---|---|
| Veri kaynaklarını kim sağlıyor? | Bölüm 2'deki envanter tablosu — çoğu kurumda zaten var |
| Aylık 1M yorumda fatura ne? | ~$450–500 (bölüm 5.3) |
| Referans müşteri / canlı kullanım var mı? | Dürüst cevap + backtest çıktısını kanıt olarak öne çıkar |
| Scraping yasal mı? | Bölüm 4'ün üç katmanlı ayrımı |
| Neden mevcut sosyal dinleme aracımız yetmiyor? | Onlar sinyal *toplar*, skor *üretmez*. Kalibrasyon, sönümleme ve tarihsel birleştirme katmanı yok |

---

## 9. Açık maddeler

- [ ] Kurumun mevcut sosyal dinleme aboneliği tespit edilecek (lisans kapsamı,
      geçmiş arşiv derinliği, export formatı)
- [ ] Şikayet platformu kurumsal panel erişimi doğrulanacak
- [ ] Apify vs. Bright Data kararı — kurumsal satın alma sürecine hangisi daha
      uygun belge sunuyor
- [ ] Metric Pack ilk taslağı (bölüm 6, hafta 3)
- [ ] Backtest için pazar payı referans verisi (Nielsen/IRI vb.) kurumdan
      talep edilecek

---

## Kaynaklar

- [X (Twitter) API Pricing in 2026 — Postproxy](https://postproxy.dev/blog/x-api-pricing-2026/)
- [How Much Does the X API Cost in 2026? — twitterapi.io](https://twitterapi.io/blog/x-api-cost-breakdown-2026)
- [Trendyol Reviews Scraper — Apify](https://apify.com/shahidirfan/trendyol-reviews-scraper/api/python)
- [Trendyol Scraper (Studio Amba) — Apify](https://apify.com/studio-amba/trendyol-scraper)
