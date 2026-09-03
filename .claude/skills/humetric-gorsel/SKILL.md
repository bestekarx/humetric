---
name: humetric-gorsel
description: HuMetric için sosyal medya içeriği üretir — kart (LinkedIn/X/Instagram görseli), carousel PDF'i, blog yazısı, gönderi metni ve sessiz demo videosu (Remotion). Bir özelliği tek karede iddia + kanıt olarak gösteren HTML kartı yazar, PNG'ye basar, istenirse arkasına blog yazısı ve LinkedIn metni koyup yayınlar. Kullanıcı "buna görsel yap", "LinkedIn kartı hazırla", "carousel yap", "bunun blogunu yaz", "LinkedIn postu yaz", "video yap", "sosyal medyada paylaşalım" dediğinde çalıştır.
---

# HuMetric sosyal medya içerik üretimi

Bir özellikten beş çıktı üretilebilir: **kart** (kare PNG), **carousel PDF**,
**blog yazısı**, **gönderi metni**, **video** (sessiz UI demosu). Kart/PDF/
blog/gönderi zinciri için kart her zaman ilk adım, gerisi opsiyonel — ama
**video bu zincirden bağımsız**: kullanıcı doğrudan "video yap" dediğinde
kart adımını atla, direkt **E. Video**'ya git.

Sistem, yan yana duran site deposunda yaşıyor (bu deponun köküne göre):
`../humetric-site/docs/sunum/social/`

```
KURAL.md      ← kural kitabı (TR). HER ZAMAN önce bunu oku.
RULES.md      ← aynısının İngilizcesi
MEKANIZMA.md  ← motorun olgu kaydı: uç noktalar, formüller, metrik anahtarları
system.css    ← tek stil kaynağı. Kart içine inline stil YAZMA.
_sablon.html  ← hazır düzenler; birini bırak, kalanını sil.
fonts/        ← yerel woff2; kartlar ağdan font çekmez
lint.sh       ← denetim (yapı + geometri). lint.py ve check.js'i çalıştırır.
export.sh     ← PNG. render.mjs üzerinden tek Chrome ile.
pdf.sh        ← PNG'leri carousel PDF'ine dizer (LinkedIn document post)
tr/ en/       ← kartlar (her kartın iki dilde eşi olur)
out/tr/ out/en/ out/pdf/ ← çıktılar
```

Blog ise **humetric-site deposunda**, gitignore'un dışında:
`backend/content/blog/{tr,en}/` + `backend/public/blog-assets/images/`.

---

## A. Kart (her zaman)

1. **`KURAL.md`'yi oku.** Anatomi, renk anlamları, kanıt kuralı, karakter
   bütçeleri, yardımcı sınıflar ve kontrol listesi orada. Bu dosya kısaltılmış
   hâli değil — kuralı oradan uygula.

2. **Seriyi ve dili belirle.** `A` = giriş/carousel (ürün nedir); `B` = saha
   ziyareti senaryosu; `C` = çağrı merkezi senaryosu; `P` = yatay afiş;
   sayısal seri = tek konuluk mekanizma kartı. Yeni bir sektör anlatılacaksa
   yeni harf aç. Kullanıcı aksini söylemedikçe **her kartı iki dilde de** üret;
   çeviri kuralları KURAL.md §8'de.

3. **Veriyi doğrula — `MEKANIZMA.md`'den.** Uç noktalar, formüller, değer
   aralıkları ve bütün metrik anahtarları orada. **Motor kaynağını tarama.**
   MEKANIZMA.md'nin kapsamadığı bir iddia varsa `humetric` deposuna bak ve
   bulguyu MEKANIZMA.md'ye ekle. Yeni bir pack eklendiyse
   `./lint.sh --sync-keys` çalıştır, yoksa lint anahtarları reddeder.

   Canlı sayı gerekiyorsa `humetric` MCP'sinden **oku** (`humetric_get_pack`,
   `humetric_explain_metric` — `contributions` listesi her sinyalin ham
   değerini verir). Sinyal göndermek/pack yayınlamak yazma işlemidir, kullanıcı
   açıkça istemeden yapma.

4. **Kartı yaz.** `_sablon.html`'i kopyala, `tr/NN-konu.html` olarak adlandır,
   sonra `en/` karşılığını yaz. Varsayılan Düzen A (sol ham girdi / sağ
   sistemin kararı).

5. **Denetle: `./lint.sh tr/… en/…`.** Temiz çıkana kadar düzelt. `lint.py`
   yapıyı, `check.js` tarayıcıda geometriyi ölçer.

   **PNG'yi Read ile okuma.** Lint'in yakaladığı kusurlar zaten yazılı
   çıkıyor. Estetik bir son bakış gerekiyorsa `./export.sh --check <kart>` ve
   `out/_check/` altındaki 800px görüntüyü oku — tek bakış, yarı maliyet.
   Yeni bir bileşen (yeni CSS sınıfı) eklediysen bu bakışı **yap**: lint
   `.et` gibi her alanı denetlemiyor, kelime ortadan bölünebiliyor.

6. **Bas.** `./export.sh tr/… en/…` — sadece değişen kartlar.

7. **Kayıt.** `KURAL.md` ve `RULES.md` sonundaki tabloya birer satır ekle;
   yeni seri açtıysan sıra açıklamasını da yaz.

---

## B. Carousel PDF (kullanıcı "carousel" / "PDF" dediyse)

```bash
./pdf.sh out/pdf/humetric-<konu>-tr.pdf out/tr/C1-*.png out/tr/C2-*.png ...
```

LinkedIn'de bu **belge (document post)** olarak yüklenir, tek tek görsel
olarak değil — kaydırmalı çıkar. 5-8 sayfa ideal. Her dil için ayrı PDF.

---

## C. Blog yazısı (kullanıcı "blog" dediyse)

Kartlar "şuna bak", blog "şunu kullan" diyor. Ayrıntılı kurallar
**KURAL.md §12**'de — dosya yerleşimi, frontmatter, görsel gömme, anahtar
dili, sonda tam YAML.

İki şeyi oradan tekrar ediyorum, çünkü en sık burada hata yapılıyor:

- **Jargon yasağı.** `202`, `SELECT FOR UPDATE SKIP LOCKED`, tablo adları,
  HTTP kodları, iç alan adları yazıya girmez. Adını değil, ne yaptığını yaz.
  Mekanizma (formül, gerçek sayılar, kanıt cümleleri) kalır; plumbing gider.
  Karşılık tablosu KURAL.md §12'de.
- **Doğruluk.** Bir iddiayı yazmadan önce motorda doğrula, özellikle KVKK
  başlığı altında. Daraltmak, sonradan düzeltmekten ucuz. Saklananı da açıkça
  söyle.

---

## D. Gönderi metni (her zaman sun, istenmese bile)

İskelet ve ton **KURAL.md §11**'de: sorun → yaygın çözüm ve neden yetmediği →
bizim yaptığımız → genelleme. Mühendis defteri tonu, emoji yok, en fazla 3
hashtag.

- **Sahneyle aç, tezle değil.** İlk iki satır "daha fazlasını gör" kesiğinin
  üstünde kalan tek şey. Akan paragraf yaz, peş peşe vurucu cümle değil.
- **Kanıtı ham bırak:** metrik değeri, altında alıntı cümlesi.
- Link gövdeye değil ilk yoruma; gönderiyi "linki ilk yorumda" ile bitir.
- **Metni kullanıcıya markdown alıntı bloğuyla (`>`) verme** — terminalde `▎`
  çıkıyor, kopyalanınca metne karışıyor. Düz kod bloğu kullan.
- TR ve EN, ikisini birden yaz.

---

## E. Video (kullanıcı "video yap" / "video oluştur" dediyse)

`video/` altında Remotion tabanlı, sessiz (anlatımsız) bir UI demo şablonu
var — node graph'ın (sinyal → extractor → curator → metrikler) sırayla
belirmesi, üstte sayaçlar ve tek bir "flag" (uyarı) node'u. Marka paleti ve
fontlar `system.css` ile birebir aynı (`video/src/theme.ts`).

1. **`video/README.md`'yi oku.** İçeriği değiştirmenin tek yolu
   `video/src/story.ts` — component dosyalarına dokunma.
2. **Senaryoyu `story.ts`'e yaz.** Node'lar (`x`/`y`, `appearAt` frame,
   `state`), başlık metinleri (`statusBeats`), kaynak chip'i. Metrik
   anahtarlarını uydurma — MEKANIZMA.md'den veya ilgili pack YAML'ından al.
3. **Önizle:** `cd video && npm run preview` (Remotion Studio).
4. **Bas:** `npm run render` → `video/out/signal-flow.mp4`.
5. Ses/anlatım eklenmesi istenirse (TTS, senaryo metni) önce metni onaya
   sun — [[feedback_script_first_video]] kuralı burada da geçerli, anlatımlı
   sürüm ayrı bir iştir.

`docs/sunum/` gitignore'da olduğu için video projesi ve render çıktıları
depoya gitmez.

---

## F. Yayın (kullanıcı "yayınla" / "canlıya al" dediyse)

1. **Yerelde bak:** humetric-site'ta `npm run start:backend` →
   `http://localhost:3001/blog/<slug>`. Render'ını görmeden basma.
2. **Commit'i daralt.** `docs/sunum/` gitignore'da — kartlar ve PDF'ler
   yerelde kalır. Sadece blog `.md`'si ve `blog-assets/images/` gider.
   Depoda kullanıcının ilgisiz değişiklikleri olabilir; **yalnızca kendi
   dosyalarını stage'le.**
3. **Push = deploy.** `main`'e push Dokploy'da dağıtımı kendisi tetikliyor.
   **Elle ikinci bir deploy başlatma.** Webhook'un işini bitirmesini bekle,
   sonra canlı URL'i doğrula.

---

## Değişmezler

- Sol = ham girdi, sağ = sistemin kararı. Ters çevirme.
- Soldaki `.hl` vurgusu ile sağdaki `<q>` alıntısı **birebir aynı metin**
  (ya da `<q>`, `.hl`'in birebir alt dizesi). Karantina (`.hold`) kartındaki
  alıntı sol panelde **gerçekten bulunmamalı** — lint ikisini de denetliyor.
- Sarı = beklemede/karantina (hata değil), kırmızı = olumsuz değer/reddedildi.
- Gölge, gradyan, ikon, stok görsel yok. En küçük punto 20px.
- Görselde müşteri adı, gerçek tesis adı, IP, iç alan adı geçmez.
- Metrik anahtarı uydurma: MEKANIZMA.md §7'de (ya da EN senaryolar için
  `en-keys.txt`'de) olmayan anahtarı lint reddeder.
- Kare kartta `.sub`'ı `.split` ile birlikte kullanma — KURAL.md §3.

## Yeni bileşen gerekirse

`system.css`'e sınıf ekle, kartın içine inline stil yazma. Marka paleti
`docs/sunum/index.html` ile senkron; renk değeri uydurma. Yeni sınıfı
KURAL.md §3 ve RULES.md'deki yardımcı sınıf listesine de yaz.

## Maliyet notu

Kart başına ~50k token / ~75 saniye. Üç şey sayesinde: motoru taramak yerine
MEKANIZMA.md okumak, PNG'ye bakmak yerine lint çalıştırmak, kart başına Chrome
açmak yerine tek Chrome üzerinden basmak. Akışı kısaltmak isteyen buradan
başlamasın — bu üçü zaten kısaltılmış hâli.
