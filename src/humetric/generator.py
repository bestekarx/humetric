"""Realistic Turkish signal and entity generator for HuMetric benchmarks.

Generates entities and free-text signals (in Turkish) for all four production
metric packs:
  - tesis (hotel property)
  - isci (field service worker)
  - cari (customer / account)
  - bolge_muduru (regional manager)

Diversity per entity type:
  - Pool of ~100 realistic entity profiles (names, cities, companies).
  - ~15-30 distinct signal scenarios per entity, mixing positive, negative,
    mixed, and neutral sentiment so the LLM extractor produces varied metric
    values.

Usage (as module):
    from humetric.generator import Generator

    gen = Generator(seed=42)
    entities, signals = gen.generate(
        entity_counts={"isci": 20, "tesis": 20, "cari": 15, "bolge_muduru": 10},
        signals_per_entity={"isci": 25, "tesis": 20, "cari": 20, "bolge_muduru": 20},
    )
    # entities: list[dict] with id, entity_type, fields, free_text
    # signals:  list[dict] with entity_id, entity_type, text
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Data pools — Turkish names, cities, company names, etc.
# ---------------------------------------------------------------------------

_FIRST_NAMES = [
    "Ahmet", "Mehmet", "Mustafa", "Ali", "Hüseyin", "Emre", "Mehmet", "Yusuf",
    "İbrahim", "Hasan", "Ayşe", "Fatma", "Emine", "Hatice", "Zeynep", "Merve",
    "Elif", "Esra", "Büşra", "Seda", "Cem", "Burak", "Can", "Deniz", "Eren",
    "Umut", "Koray", "Barış", "Serkan", "Volkan", "Gökhan", "Tolga", "Ozan",
    "Mert", "Onur", "Selim", "Turgay", "Hakan", "Murat", "Aylin", "Derya",
    "Bora", "Tarık", "Fırat", "Ege", "Alp", "Kaya", "Arda", "Sarp", "Kaan",
]

_LAST_NAMES = [
    "Yılmaz", "Kaya", "Demir", "Çelik", "Yıldız", "Öztürk", "Aydın",
    "Yıldırım", "Özdemir", "Arslan", "Doğan", "Kılıç", "Aslan", "Çetin",
    "Kara", "Koç", "Kurt", "Özkan", "Şimşek", "Polat", "Taş", "Erdoğan",
    "Güneş", "Bulut", "Koçak", "Turan", "Kaplan", "Çiftçi", "Ay", "Sert",
    "Sağlam", "Ateş", "Keskin", "Uzun", "Ersoy", "Çevik", "Sezgin", "Aksoy",
    "Acar", "Tuncer", "Albayrak", "Atalay", "Yalçın", "Şahin", "Avcı", "Küçük",
]

_CITIES = [
    "İstanbul", "Ankara", "İzmir", "Bursa", "Adana", "Gaziantep", "Konya",
    "Antalya", "Kayseri", "Mersin", "Eskişehir", "Diyarbakır", "Samsun",
    "Denizli", "Şanlıurfa", "Malatya", "Kahramanmaraş", "Trabzon", "Erzurum",
    "Van", "Elazığ", "Aydın", "Manisa", "Balıkesir", "Sakarya", "Kocaeli",
    "Tekirdağ", "Çanakkale", "Muğla", "Bodrum", "Alanya", "Fethiye",
]

_DISTRICTS = [
    "Kadıköy", "Beşiktaş", "Üsküdar", "Bakırköy", "Çankaya", "Keçiören",
    "Konak", "Karşıyaka", "Osmangazi", "Nilüfer", "Seyhan", "Çukurova",
    "Merkez", "Melikgazi", "Talas", "Muratpaşa", "Tepebaşı", "İlkadım",
    "Pamukkale", "Haliliye", "Battalgazi", "Ortahisar", "Yakutiye",
]

_COMPANY_NAMES = [
    "Anadolu Ticaret", "Bosphorus Yapı", "Çelik Endüstri", "Denizli Tekstil",
    "Ege Gıda", "Fırat Ticaret", "Güney Lojistik", "İkizler Makine",
    "Karadeniz Sanayi", "Marmara Depo", "Özkan Ticaret", "Pamukkale Tekstil",
    "Sakarya Servis", "Trakya Gıda", "Uludağ Metal", "Vadi Ticaret",
    "Doğuş Yapı", "Batı Servis", "Kuzey Lojistik", "Güven Ticaret",
    "Akdeniz Gıda", "İçel Tarım", "Konya Un", "Bursa Makine",
    "Sancak Ticaret", "Ata Enerji", "Önder Ambalaj", "Mega Yapı",
    "Şimşek Ticaret", "Kırıkkale Servis", "Avcılar Kimya", "Pendik Metal",
]

_TESIS_NAMES = [
    "Marina Bay Otel", "Zeytin Konak", "Panorama Resort", "Liman Otel",
    "Asma Yaprak Butik", "Deniz Feneri Otel", "Çınaraltı Konak", "Sahil Palas",
    "Gökçe Resort", "Taşhan Butik Otel", "Mavi Koy Otel", "Bahçeli Konak",
    "Yalı Suites", "Kayalık Resort", "Zümrüt Otel", "Rüzgargülü Otel",
    "Beyaz Ev Butik", "Antik Kapı Konak", "Palmiye Resort", "Sedir Otel",
    "Kumsal Suites", "Değirmen Konak", "Bulut Tepe Otel", "Narin Butik",
    "Yıldız Bahçe Otel", "Köprübaşı Konak", "Turkuaz Bay Resort", "Ada Otel",
    "Meltem Suites", "Fener Konak", "Papatya Otel", "Selvi Resort",
]

_TESIS_TIPLERI = [
    "şehir oteli", "resort", "butik otel", "termal otel",
    "apart otel", "konak", "dağ oteli", "havalimanı oteli",
]

_SERVICE_AREAS = [
    "İstanbul Avrupa", "İstanbul Anadolu", "Ankara Merkez", "İzmir ve Çevresi",
    "Bursa Bölgesi", "Adana Çukurova", "Gaziantep Güneydoğu", "Konya İç Anadolu",
    "Antalya Batı Akdeniz", "Kayseri Kapadokya", "Mersin Doğu Akdeniz",
    "Eskişehir Batı Anadolu", "Samsun Karadeniz", "Trabzon Doğu Karadeniz",
    "Denizli Ege", "Diyarbakır Güneydoğu",
]

_REGIONS = [
    "Marmara", "Ege", "Akdeniz", "İç Anadolu", "Karadeniz",
    "Doğu Anadolu", "Güneydoğu Anadolu",
]

_SKILLS = [
    "Elektrik Tesisat", "Su Tesisat", "Klima Montaj/Bakım", "Kombi Bakım",
    "Doğalgaz Tesisat", "Genel Tesisat", "Beyaz Eşya Tamiri", "Isıtma Sistemleri",
    "Soğutma Sistemleri", "Havalandırma", "Yangın Tesisat", "Güvenlik Sistemleri",
    "Çatı Onarım", "Boya/Badana", "Mobilya Montaj", "Fayans/Seramik",
    "Elektrikli Ev Aletleri", "Kamera Sistemleri", "Otomasyon", "Yangın Söndürme",
]

_WORK_AREAS = [
    "Saha Montaj", "Arıza Giderme", "Periyodik Bakım", "Acil Servis",
    "Kurulum Projeleri", "Teknik Destek",
]

_KANALLAR = [
    "online rezervasyon platformu", "tur operatörü", "kurumsal anlaşma",
    "acente", "doğrudan rezervasyon", "sadakat programı",
]

_ODA_TIPLERI = [
    "standart oda", "deniz manzaralı oda", "aile odası", "suit",
    "engelli dostu oda", "bahçe katı oda", "çatı katı suit", "tek kişilik oda",
]

_HIZMETLER = [
    "kahvaltı", "oda servisi", "havuz", "spa",
    "otopark", "havalimanı transferi", "çamaşırhane", "toplantı salonu",
]


@dataclass
class EntityDef:
    id: str
    entity_type: str
    fields: dict[str, Any]
    free_text: str | None = None


@dataclass
class SignalDef:
    entity_id: str
    entity_type: str
    text: str
    external_id: str | None = None


# ---------------------------------------------------------------------------
# Scenario templates — rich, varied, realistic Turkish business language
# ---------------------------------------------------------------------------

# Each entry: (template_str, sentiment_hint)
# Template placeholders use {key} format — the generator fills them.

# ============================================================ ISCI ============================================================

_ISCI_POSITIVE_TEMPLATES = [
    # Dakiklik (punctuality)
    (
        "{ad} {soyad} bu ay hiç gecikme yapmadı. Tüm randevularına "
        "zamanında, hatta çoğu zaman 10-15 dakika erken gitti. {bolge} "
        "bölgesinde müşteriler dakikliğinden çok memnun.",
        "dakiklik",
    ),
    (
        "{ad} {soyad} bugün {ilce}'deki servis çağrısına tam saatinde "
        "geldi. Sabah trafik yoğunluğuna rağmen çok disiplinli. Müşteri "
        "notu: 'Tam zamanında geldi, teşekkür ederiz.'",
        "dakiklik",
    ),
    (
        "Çalışan performans raporu: {ad} {soyad}, 3 aylık dönemde sıfır "
        "gecikme. Planlanan 78 randevunun tamamına zamanında katılım "
        "sağladı. Bölge {bolge}, ekip lideri onaylı.",
        "dakiklik",
    ),
    (
        "{ad} {soyad} sabah mesaisine her gün 07:50'de geliyor, "
        "servis aracını kontrol ediyor ve 08:00'de sahaya çıkıyor. "
        "Hiç mazeret izni kullanmadığı bir ay daha geçirdi.",
        "dakiklik",
    ),

    # Titizlik (work quality)
    (
        "{ad} {soyad} {ilce}'deki {beceri} işini olağanüstü temiz "
        "yaptı. Müşteri: 'Kabloları topladı, çalıştığı alanı "
        "süpürdü, malzemeleri düzenli bıraktı.' İş bitirme raporu "
        "fotoğraflı, eksiksiz.",
        "titizlik",
    ),
    (
        "Kalite denetim sonucu: {ad} {soyad}, {beceri} işinde %98 "
        "temizlik skoru. Kullanılan malzeme israfı sıfıra yakın. "
        "Takım çantası tertipli, aletler kalibrasyonlu.",
        "titizlik",
    ),
    (
        "{ad} {soyad} bugün yaptığı {beceri} kurulumunda standart "
        "çalışma prosedürüne harfiyen uydu. Koruyucu ekipman tam, "
        "iş alanı temizliği örnek gösterilecek düzeyde.",
        "titizlik",
    ),
    (
        "Müşteri geri bildirimi: '{ad} Bey işini çok özenli yaptı, "
        "giderken çöpünü bile topladı. Böyle çalışan az bulunur.' "
        "Konu: {beceri} bakım servisi, {ilce}.",
        "titizlik",
    ),

    # Teknik beceri
    (
        "{ad} {soyad} karmaşık bir {beceri} arızasını 45 dakikada "
        "çözdü. Başka bir ekibin 3 saat uğraşıp yapamadığı işi tek "
        "başına halletti. Müşteri hayran kaldı.",
        "teknik_beceri",
    ),
    (
        "{ad} {soyad} bu hafta {beceri} alanında yeni nesil cihazların "
        "kurulum eğitimini başarıyla tamamladı. Eğitmen yorumu: "
        "'En hızlı öğrenen katılımcı.' Sertifika aldı.",
        "teknik_beceri",
    ),
    (
        "Teknik değerlendirme: {ad} {soyad}, {beceri} dalında uzman "
        "seviyesinde. Arıza tespit süresi ortalaması 12 dakika "
        "(şirket ortalaması 28 dk). Problem çözme yeteneği üstün.",
        "teknik_beceri",
    ),
    (
        "{ad} {soyad} {ilce}'deki acil çağrıda, standart dışı bir "
        "{beceri} sorununu yaratıcı bir çözümle 1 saatte giderdi. "
        "Diğer teknisyen: 'Bunu nasıl düşündüğünü anlamadım, adam "
        "deha.'",
        "teknik_beceri",
    ),

    # Communication
    (
        "{ad} {soyad} müşteriye {beceri} yapılacak işleri baştan "
        "sona net bir dille anlattı. Müşteri: 'Her aşamayı anladım, "
        "teknik konuları bile basitleştirerek anlatabiliyor.' "
        "Memnuniyet puanı: 10/10.",
        "iletisim",
    ),
    (
        "Müşteri şikayeti çözümü: {ad} {soyad}, {ilce}'deki bir "
        "müşterinin {beceri} ile ilgili şikayetini birebir görüşerek "
        "halletti. Özür diledi, ekstra garanti verdi, müşteri "
        "şikayetini geri çekti.",
        "iletisim",
    ),
    (
        "{ad} {soyad} bugün yaşlı bir müşteriye fazladan 20 dakika "
        "ayırarak {beceri} cihazının kullanımını tek tek gösterdi. "
        "Müşteri arayıp teşekkür etti: 'Evladım gibi ilgilendi.'",
        "iletisim",
    ),
    (
        "Ekip içi değerlendirme: {ad} {soyad} çağrı merkeziyle çok "
        "iyi koordine oluyor. İş emri güncellemelerini anında yapıyor, "
        "gecikme durumunda müşteriyi mutlaka arıyor.",
        "iletisim",
    ),
]

_ISCI_NEGATIVE_TEMPLATES = [
    # Dakiklik
    (
        "{ad} {soyad} bu ay 4 randevuya geç kaldı. {ilce}'deki "
        "müşteri 45 dakika bekledi ve çağrı merkezine şikayet etti. "
        "Gerekçe: 'Trafik vardı' — ancak 3. kez aynı mazeret.",
        "dakiklik",
    ),
    (
        "{ad} {soyad} sabah mesaisine son 2 haftadır sürekli 15-20 "
        "dakika geç geliyor. Ekip lideri {gecen_ay} ayında 3 kez "
        "uyardı. Bugün yine geç kaldı, {ilce} bölgesindeki ilk "
        "randevusu iptal oldu.",
        "dakiklik",
    ),
    (
        "Müşteri şikayeti: '{ad} Bey randevuya 1 saat geç geldi, "
        "haber vermedi. Kapıda bekledim, işe geç kaldım.' "
        "Tarih: {tarih}, Lokasyon: {ilce}. 2. kez aynı şikayet.",
        "dakiklik",
    ),

    # Titizlik
    (
        "{ad} {soyad} {ilce}'deki {beceri} işinden sonra çalışma "
        "alanını dağınık bıraktı. Kesilmiş kablolar, vida artıkları "
        "yerde. Müşteri fotoğraf çekip gönderdi. İş emri kapandı "
        "ama kalite kontrol başarısız.",
        "titizlik",
    ),
    (
        "Kalite denetim: {ad} {soyad}, {beceri} işinde koruyucu "
        "ekipman kullanmadı. Malzeme israfı yüksek — 3 metre fazla "
        "boru kesmiş. İş bitiminde çöpler toplanmamış.",
        "titizlik",
    ),
    (
        "Müşteri: '{ad} Bey işi aceleyle yapıp gitti, duvarda "
        "matkap izleri kaldı, sıva döküldü. Tamir etmeden çıktı.' "
        "Konu: {beceri} kurulum, {ilce}.",
        "titizlik",
    ),

    # Teknik beceri
    (
        "{ad} {soyad} bugün basit bir {beceri} arızasını teşhis "
        "edemedi, yardım çağırmak zorunda kaldı. İkinci ekip geldi "
        "ve sorunun sigorta olduğunu anladı. Müşteri 2 saat boşa "
        "bekledi.",
        "teknik_beceri",
    ),
    (
        "Teknik eğitim sonucu: {ad} {soyad}, {beceri} yenileme "
        "sınavında 100 üzerinden 45 aldı. Eğitmen: 'Temel kavramları "
        "bilmiyor, tekrar kursa katılmalı.' Sertifika yenileme "
        "başarısız.",
        "teknik_beceri",
    ),

    # Communication
    (
        "{ad} {soyad} müşteriye yapılan {beceri} işlemini hiç "
        "açıklamadan faturayı bırakıp gitti. Müşteri faturayı "
        "anlamadı, çağrı merkezini arayıp 'Ne yapıldığını bilmiyorum' "
        "dedi.",
        "iletisim",
    ),
    (
        "Müşteri iletişim kaydı: {ad} {soyad}, {ilce}'deki müşteriye "
        "'Sana ne, ben işimi yapar giderim' demiş. Müşteri çok kırgın, "
        "bir daha aynı firmayı çağırmayacağını söylüyor.",
        "iletisim",
    ),
    (
        "{ad} {soyad} çağrı merkezinin aktardığı müşteri notunu "
        "okumamış, yanlış adrese gitmiş. İş emri iptal. Sebep: telefon "
        "mesajlarını kontrol etmemiş.",
        "iletisim",
    ),
]

_ISCI_MIXED_NEUTRAL_TEMPLATES = [
    (
        "{ad} {soyad} {ilce}'deki {beceri} işini tamamladı. İş "
        "süresi tahmin edilenin %10 üzerinde ama sonuç tatmin edici. "
        "Müşteri nötr — ne övdü ne şikayet etti.",
        "karisik",
    ),
    (
        "{ad} {soyad} bugün standart bir {beceri} bakım yaptı. "
        "Rutin iş, olağandışı bir durum yok. Müşteri imza attı, "
        "iş emri normal kapandı.",
        "karisik",
    ),
    (
        "{ad} {soyad} 2 haftadır raporlu. {gecen_ay} ayı performansı "
        "ortalamaydı. İşe dönüşü bekleniyor.",
        "karisik",
    ),
    (
        "{ad} {soyad} {ilce} bölgesinde {beceri} alanında çalışıyor. "
        "Bu ayki iş emri sayısı: {sayi}. Geçen aya göre benzer. "
        "Bölge yöneticisi henüz değerlendirme yapmadı.",
        "karisik",
    ),
]

# ============================================================ TESIS ============================================================

_TESIS_POSITIVE_TEMPLATES = [
    # Temizlik ve bakim
    (
        "{tesis} denetim raporu: Oda temizlik skoru 96/100, ortak alanlarda "
        "uygunsuzluk yok. Açık arıza kaydı sıfır. Denetçi notu: "
        "'Bakım ekibi eksikleri gün içinde kapatıyor.'",
        "temizlik_ve_bakim",
    ),
    (
        "{tesis} misafir yorumu: 'Odaya girdiğimizde her yer pırıl pırıldı, "
        "banyoda tek bir leke yoktu. {oda_tipi} tercih ettik, beklentimizin "
        "üstündeydi.' Temizlik puanı 9.4/10.",
        "temizlik_ve_bakim",
    ),
    (
        "{tesis} yenileme sonrası ilk ay: {sayi} odada mobilya ve klima "
        "değişimi tamamlandı. Bakım kaynaklı şikayet oranı %70 düştü.",
        "temizlik_ve_bakim",
    ),

    # Personel ilgisi
    (
        "{tesis} misafir anketi: Personel ilgisi 5 üzerinden 4.8. "
        "Özellikle resepsiyonun karşılama hızı ve sorun çözme tutumu övülmüş. "
        "'Gece yarısı aradık, 10 dakikada çözdüler.'",
        "personel_ilgisi",
    ),
    (
        "{tesis} misafir yorumu: 'Kahvaltıda çocuğumuz için ayrı menü "
        "hazırladılar, kimse istemeden düşünmüşler. Böyle bir ilgiyi "
        "beklemiyorduk.' Tavsiye skoru %94.",
        "personel_ilgisi",
    ),
    (
        "{tesis} gizli müşteri ziyareti: Check-in 3 dakikada tamamlandı, "
        "{hizmet} talebi anında karşılandı. Rapor: 'Ekip eğitimli ve istekli.'",
        "personel_ilgisi",
    ),

    # Konfor ve sessizlik
    (
        "{tesis} {oda_tipi} geri bildirimi: 'Yatak çok rahattı, dışarıdan "
        "hiç ses gelmedi, klima sessiz çalışıyordu.' Konfor puanı 9.1/10.",
        "konfor_ve_sessizlik",
    ),
    (
        "{tesis} ses yalıtımı yatırımı sonrası gürültü şikayeti son çeyrekte "
        "12'den 2'ye düştü. {hizmet} alanı ile oda katları arasındaki geçiş "
        "yeniden düzenlendi.",
        "konfor_ve_sessizlik",
    ),

    # Tekrar gelme egilimi
    (
        "{tesis} misafir yorumu: '{sehir} her gelişimizde burada kalıyoruz, "
        "bu 4. konaklamamız. Arkadaşlarımıza da tavsiye ettik.' "
        "Sadakat programı üzerinden tekrar rezervasyon oranı %38.",
        "tekrar_gelme_egilimi",
    ),
    (
        "{tesis} {kanal} üzerinden gelen misafirlerin %41'i ikinci kez "
        "konakladı. İptal oranı %3 ile bölge ortalamasının altında.",
        "tekrar_gelme_egilimi",
    ),
]

_TESIS_NEGATIVE_TEMPLATES = [
    # Temizlik ve bakim
    (
        "{tesis} denetim raporu: {sayi} odada açık arıza kaydı var, "
        "bazıları 3 haftadır kapatılmamış. Temizlik skoru 61/100. "
        "Ortak tuvaletlerde uygunsuzluk tespit edildi.",
        "temizlik_ve_bakim",
    ),
    (
        "{tesis} misafir yorumu: 'Odaya girdiğimizde çarşaflar değişmemişti, "
        "banyoda küf kokusu vardı. Oda değiştirmek zorunda kaldık.' "
        "Temizlik puanı 4.2/10.",
        "temizlik_ve_bakim",
    ),
    (
        "{tesis} bakım kaynaklı şikayetler üst üste 3 aydır artıyor: "
        "sıcak su kesintisi, asansör arızası, {oda_tipi} klimalarında sorun. "
        "Teknik ekip yetersiz kalıyor.",
        "temizlik_ve_bakim",
    ),

    # Personel ilgisi
    (
        "{tesis} misafir şikayeti: 'Resepsiyonda 25 dakika bekledik, "
        "kimse ilgilenmedi. Sorduğumuz soruya yanıt bile alamadık.' "
        "Personel puanı 3.1/10.",
        "personel_ilgisi",
    ),
    (
        "{tesis} gizli müşteri ziyareti: {hizmet} talebi iki kez unutuldu, "
        "şikayet iletildiğinde özür dilenmedi. Rapor: 'Tavsiye edilmez.'",
        "personel_ilgisi",
    ),

    # Konfor ve sessizlik
    (
        "{tesis} yorumlarında tekrar eden gürültü şikayeti: "
        "'Koridordan gelen sesler yüzünden sabaha kadar uyuyamadık.' "
        "Son ayda benzer 9 yorum var.",
        "konfor_ve_sessizlik",
    ),
    (
        "{tesis} {oda_tipi} geri bildirimi: 'Yatak çok sertti, klima "
        "gürültülüydü, pencere tam kapanmıyordu.' Konfor puanı 4.5/10.",
        "konfor_ve_sessizlik",
    ),

    # Tekrar gelme egilimi
    (
        "{tesis} misafir yorumu: 'Bir daha gelmeyiz, {sehir} bu fiyata "
        "çok daha iyi alternatif var.' Erken çıkış talebi kayda geçti.",
        "tekrar_gelme_egilimi",
    ),
    (
        "{tesis} {kanal} kaynaklı rezervasyonlarda iptal oranı %19'a çıktı. "
        "Tekrar konaklama oranı bir yılda %31'den %12'ye düştü.",
        "tekrar_gelme_egilimi",
    ),
]

_TESIS_MIXED_NEUTRAL_TEMPLATES = [
    (
        "{tesis} bu ay bölge ortalamasında: doluluk %72, misafir puanı 7.8. "
        "Ne öne çıkan bir başarı ne de belirgin bir sorun var.",
        "karisik",
    ),
    (
        "{tesis} misafir yorumu: 'Konum ve temizlik iyiydi ama {hizmet} "
        "beklediğimiz gibi değildi. Fiyatına göre normal.'",
        "karisik",
    ),
    (
        "{tesis} rutin denetimi tamamlandı, kritik bulgu yok. "
        "{oda_tipi} kategorisinde küçük eksikler raporlandı, "
        "tolere edilebilir düzeyde.",
        "karisik",
    ),
]

# ============================================================ CARI ============================================================

_CARI_POSITIVE_TEMPLATES = [
    # Payment habit
    (
        "{firma} müşterisi son 12 aydır tüm faturalarını vadesinde "
        "veya erken ödedi. Ortalama ödeme süresi 3 gün (vadeden önce). "
        "Tahsilat zorluğu: sıfır.",
        "odeme_aliskanligi",
    ),
    (
        "{firma} cari hesap: 2025 yılı boyunca toplam 48 fatura, "
        "tamamı vadesinde. Otomatik ödeme talimatı var, banka "
        "havalesiyle düzenli ödüyor. Kredi risk skoru: düşük.",
        "odeme_aliskanligi",
    ),
    (
        "{firma} müşterisi bugün peşin ödeme yaparak 3 aylık "
        "bakım paketi satın aldı. Muhasebe notu: 'En sorunsuz "
        "müşterilerimizden.'",
        "odeme_aliskanligi",
    ),

    # Communication
    (
        "{firma} yetkilisi {muhatap} Bey/Hanım ile iletişim çok "
        "hızlı. Gönderilen teklife 1 saat içinde dönüş yapıyor, "
        "telefonlara her zaman çıkıyor. Randevu iptalleri en az "
        "24 saat önceden haber veriliyor.",
        "iletisim",
    ),
    (
        "{firma} ile yazışmalar net ve düzenli. {muhatap} Bey "
        "teknik konulara hakim, taleplerini açıkça iletiyor. "
        "Geri dönüş hızı ortalamanın çok üstünde.",
        "iletisim",
    ),
    (
        "{firma} müşterisi acil durumda bile ulaşılabilir. "
        "Cumartesi günü çıkan acil arızada {muhatap} Bey'e "
        "hemen ulaşıldı, onay 5 dakikada alındı.",
        "iletisim",
    ),

    # Repeat business
    (
        "{firma} müşterisi son 2 yılda 6 proje tekrarı verdi. "
        "2025'te toplam iş hacmi 350.000 TL. Sadakat seviyesi "
        "çok yüksek, başka firmayla çalışmayı düşünmüyor.",
        "is_tekrari",
    ),
    (
        "{firma} bu yıl 4. kez yıllık bakım sözleşmesi yeniledi. "
        "2020'den beri kesintisiz çalışıyoruz. Tavsiye ettiği "
        "2 yeni müşteri daha kazandırdı.",
        "is_tekrari",
    ),
    (
        "{firma} müşterisi referans olarak kullanılabilecek "
        "düzeyde. Memnuniyet anketinde: 'Uzun yıllardır çalışıyoruz, "
        "güven tam.' Diğer firmalardan teklif alsa da değiştirmedi.",
        "is_tekrari",
    ),

    # Memnuniyet
    (
        "{firma} müşteri memnuniyet puanı: 10/10. Hizmet sonrası "
        "anket: 'Her şey mükemmeldi, teşekkürler.' Sosyal medyada "
        "firmamızı öven paylaşım yaptı.",
        "memnuniyet",
    ),
    (
        "{firma} yetkilisi {muhatap} Bey hizmetten çok memnun. "
        "'Ekip çok profesyonel, zamanında iş teslimi, fiyat uygun.' "
        "Yılbaşında teşekkür mektubu gönderdi.",
        "memnuniyet",
    ),
    (
        "{firma} müşterisi 'iyi ki sizi seçmişiz' diyerek hizmet "
        "değerlendirme formuna ekstra teşekkür notu yazdı. Şikayet "
        "kaydı: sıfır. Geri bildirim: tamamen olumlu.",
        "memnuniyet",
    ),
]

_CARI_NEGATIVE_TEMPLATES = [
    # Payment habit
    (
        "{firma} müşterisi faturalarında kronik gecikme var. Son "
        "3 fatura ortalama 42 gün gecikmeli ödendi. Tahsilat ekibi "
        "her seferinde aramak zorunda kalıyor.",
        "odeme_aliskanligi",
    ),
    (
        "{firma} cari hesap borcu 45.000 TL, vadesi 60 gün geçmiş. "
        "İletişim kurulamıyor, telefonlara çıkmıyor. Yasal takip "
        "dosyası açıldı.",
        "odeme_aliskanligi",
    ),
    (
        "{firma} müşterisi 'ödeyeceğim' diyerek sürekli erteliyor. "
        "Son 6 ayda 4 farklı ödeme tarihi verdi, hiçbirine uymadı. "
        "Çeklerinde karşılıksızlık riski notu var.",
        "odeme_aliskanligi",
    ),

    # Communication
    (
        "{firma} yetkilisi {muhatap} Bey'e ulaşmak çok zor. 5 kez "
        "aranmasına rağmen dönüş yapmadı. E-postalar cevapsız, "
        "randevu teyitleri gelmiyor. Proje ilerlemiyor.",
        "iletisim",
    ),
    (
        "{firma} müşterisi iş başladıktan sonra ek talepler "
        "iletiyor, onay süreçleri çok yavaş. Bir karar için "
        "2 hafta bekleniyor. İş teslim süresi uzuyor.",
        "iletisim",
    ),
    (
        "{firma} ile iletişim kaotik. {muhatap} Bey sürekli farklı "
        "şeyler söylüyor, yazılı onay vermeden sözlü talimatla iş "
        "yaptırmaya çalışıyor. Şantiye şefi rahatsız.",
        "iletisim",
    ),

    # Repeat business
    (
        "{firma} müşterisi bu yıl sadece 1 küçük iş verdi. Geçen "
        "yıl 4 proje vardı, %75 düşüş. Muhtemel sebep: rakip "
        "firmayla anlaşma yapmış olabilir.",
        "is_tekrari",
    ),
    (
        "{firma} ile son 8 aydır hiç iş yapılmadı. Teklif veriliyor "
        "ama rakip firmalar daha düşük fiyat verdiği için kaybediliyor. "
        "Sadakat seviyesi düşük.",
        "is_tekrari",
    ),

    # Memnuniyet
    (
        "{firma} müşterisi son işten hiç memnun değil. 'Teslim "
        "süresi aşıldı, malzeme kalitesi düşük, muhatap bulamadık.' "
        "Şikayet kaydı açıldı, iade talep ediyor.",
        "memnuniyet",
    ),
    (
        "{firma} anket sonucu: 5 üzerinden 1.5. Müşteri 'bir daha "
        "asla' demiş. Gerekçe: İşçilik hatalı, fatura açıklaması "
        "yok, şikayet dikkate alınmadı.",
        "memnuniyet",
    ),
    (
        "{firma} müşterisinden sektör derneğine resmi şikayet "
        "geldi. Yapılan iş standartlara uygun değilmiş. Hakem "
        "heyeti süreci başladı.",
        "memnuniyet",
    ),
]

_CARI_MIXED_NEUTRAL_TEMPLATES = [
    (
        "{firma} müşterisi düzenli ödeme yapıyor ama ufak "
        "gecikmeler oluyor (3-5 gün). Genel olarak sorun "
        "yaratmıyor. İlişki sürdürülebilir düzeyde.",
        "karisik",
    ),
    (
        "{firma} ile yılda ortalama 2-3 kez iş yapılıyor. "
        "Küçük ölçekli, memnuniyet orta düzeyde. Ne sadık "
        "müşteri ne sorunlu müşteri.",
        "karisik",
    ),
]

# ============================================================ BOLGE_MUDURU ============================================================

_BM_POSITIVE_TEMPLATES = [
    # Tesis yonetimi
    (
        "{ad} {soyad} {bolge} bölgesindeki 12 tesisi düzenli ziyaret ediyor. "
        "Haftalık ziyaret ortalaması 4, aylık 16. Tesis müdürü memnuniyet "
        "anketi: %91 olumlu. 'Her sorunumuzu dinler, çözüm üretir.'",
        "tesis_yonetimi",
    ),
    (
        "{ad} {soyad} bugün {ilce}'deki {tesis} tesisinde 3 saat kalarak "
        "yüksek sezon vardiya planına yardımcı oldu. Tesis müdürü: "
        "'Gelmeseydi bu planı yetiştiremezdik.'",
        "tesis_yonetimi",
    ),
    (
        "{ad} {soyad} geçen ay tesislerde açık arıza kapanma süresini "
        "ortalama 7 günden 3 güne indirdi. Teknik ekiple koordinasyonu "
        "sayesinde bakım kaynaklı misafir şikayeti %60 azaldı.",
        "tesis_yonetimi",
    ),
    (
        "{ad} {soyad} {bolge} bölgesinde tesisler için misafir iletişimi "
        "eğitimi başlattı. {sayi} tesiste resepsiyon ekibi eğitildi. "
        "Katılım %100, geri bildirim mükemmel.",
        "tesis_yonetimi",
    ),

    # Raporlama
    (
        "{ad} {soyad} aylık saha raporunu her ayın 2'sinde, eksiksiz ve "
        "zamanında teslim ediyor. Rapor içeriği: doluluk verileri, açık arıza "
        "durumu, tesis ziyaret notları, fotoğraflı saha analizi. "
        "Yönetim: 'Örnek rapor.'",
        "raporlama",
    ),
    (
        "{ad} {soyad} haftalık rapor formatını iyileştirdi. Artık misafir "
        "puanı trend analizlerini de ekliyor. Genel müdür: "
        "'Tüm bölgelere bu formatı öneriyorum.'",
        "raporlama",
    ),
    (
        "{ad} {soyad} bugün {ilce}'deki tesis denetim raporunu tablet "
        "üzerinden anında sisteme girdi. Fotoğraf, eksik listesi ve tesis "
        "değerlendirmesi tek seferde. Veri doğruluğu %100.",
        "raporlama",
    ),

    # Denetim takibi
    (
        "{ad} {soyad} {bolge} bölgesindeki tesislerde standartlara uygunluk "
        "denetimi yaptı. 12 tesiste hijyen ve görsel standart skoru ortalama "
        "92/100. Eksikler raporlandı, aksiyon planı hazır.",
        "denetim_takibi",
    ),
    (
        "{ad} {soyad} {tesis} tesisindeki depo denetiminde tutarsızlık "
        "yakaladı. Fiziki stok ile sistem kaydı arasında fark vardı. "
        "Anında düzeltme yaptırdı, süreç iyileştirme önerisi sundu.",
        "denetim_takibi",
    ),
    (
        "{ad} {soyad} kalite denetim turunda {bolge} bölgesinde hiçbir "
        "tesiste ciddi uygunsuzluk bulamadı. Yangın tüpleri dolu, mutfak "
        "hijyen belgeleri güncel, kaçış yolları açık. Genel müdür tebrik etti.",
        "denetim_takibi",
    ),
]

_BM_NEGATIVE_TEMPLATES = [
    # Tesis yonetimi
    (
        "{ad} {soyad} {bolge} bölgesinde tesisleri yeterince ziyaret etmiyor. "
        "Son 2 ayda sadece 3 tesis ziyareti var. Tesis müdürleri şikayetçi: "
        "'Bizi unuttu, ne zaman geleceği belli değil.'",
        "tesis_yonetimi",
    ),
    (
        "{ad} {soyad} tesislerin acil ihtiyaçlarına cevap vermiyor. "
        "{tesis} tesisinde 5 gündür sıcak su arızası var, defalarca aradılar "
        "ama dönüş alamadılar. Tesis doğrudan genel müdürlüğe şikayet etti.",
        "tesis_yonetimi",
    ),
    (
        "{ad} {soyad} {ilce}'deki yeni tesis açılışında gerekli desteği "
        "vermedi. Tesis tabela ve ekipman olmadan 2 hafta bekledi. "
        "Genel müdür duruma müdahale etmek zorunda kaldı.",
        "tesis_yonetimi",
    ),

    # Raporlama
    (
        "{ad} {soyad} geçen ayki raporunu 15 gün gecikmeli teslim etti. "
        "Rapor içeriği yetersiz: doluluk verileri eksik, tesis notları yok, "
        "fotoğrafsız. Geri çevrildi.",
        "raporlama",
    ),
    (
        "{ad} {soyad} haftalık raporunu hatalı verilerle doldurmuş. "
        "3 tesisin doluluk rakamları sistem kayıtlarıyla uyuşmuyor. "
        "Veri güvenilirliği sorgulanıyor.",
        "raporlama",
    ),

    # Denetim takibi
    (
        "{ad} {soyad} {bolge} bölgesindeki denetimde {tesis} tesisinde ciddi "
        "standart ihlalleri atlanmış. Başka bir denetçi aynı tesise "
        "gittiğinde: yangın tüpü süresi geçmiş, mutfak hijyen kaydı eksik, "
        "kaçış yolu kapalı.",
        "denetim_takibi",
    ),
    (
        "{ad} {soyad} denetim formlarını 'göstermelik' dolduruyor. "
        "Son denetimde 5 tesisi ziyaret etmeden 'denetlendi' olarak "
        "işaretlediği tespit edildi. Soruşturma başlatıldı.",
        "denetim_takibi",
    ),
]

_BM_MIXED_NEUTRAL_TEMPLATES = [
    (
        "{ad} {soyad} {bolge} bölgesinde görevine yeni başladı. "
        "Henüz tesislerle tanışma aşamasında. Performans değerlendirmesi "
        "için erken.",
        "karisik",
    ),
    (
        "{ad} {soyad} tesisleri ayda bir ziyaret ediyor — şirket standardı "
        "tam bu. Ne fazla ne eksik. Rutin devam ediyor.",
        "karisik",
    ),
]


# ---------------------------------------------------------------------------
# The Generator
# ---------------------------------------------------------------------------


class Generator:
    """Produces realistic entity + signal datasets for HuMetric benchmarks."""

    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)
        self._entity_counter: dict[str, int] = {}
        self._signal_counter: int = 0

    # ---- helpers ----

    def _pick(self, items: list[Any]) -> Any:
        return self.rng.choice(items)

    def _rand_int(self, lo: int, hi: int) -> int:
        return self.rng.randint(lo, hi)

    def _rand_date(self, days_back: int = 90) -> str:
        d = date.today() - timedelta(days=self.rng.randint(0, days_back))
        return d.isoformat()

    def _fill(self, template: str, ctx: dict[str, Any]) -> str:
        """Fill template {placeholders} with context dict values, removing
        placeholders that have no value."""
        try:
            return template.format(**ctx)
        except KeyError as exc:
            missing = exc.args[0]
            # Remove unresolvable placeholder from template and retry
            import re
            cleaned = re.sub(r"\{" + re.escape(missing) + r"\}", "", template)
            return cleaned.format(**ctx)

    def _make_context(self, entity_type: str) -> dict[str, Any]:
        """Random context for template filling."""
        return {
            "ad": self._pick(_FIRST_NAMES),
            "soyad": self._pick(_LAST_NAMES),
            "sehir": self._pick(_CITIES),
            "ilce": self._pick(_DISTRICTS),
            "bolge": self._pick(_SERVICE_AREAS if entity_type == "isci" else _REGIONS),
            "firma": self._pick(_COMPANY_NAMES),
            "tesis": self._pick(_TESIS_NAMES),
            "beceri": self._pick(_SKILLS),
            "kanal": self._pick(_KANALLAR),
            "oda_tipi": self._pick(_ODA_TIPLERI),
            "hizmet": self._pick(_HIZMETLER),
            "sayi": self._rand_int(2, 50),
            "gecen_ay": self._pick(["Ocak", "Şubat", "Mart", "Nisan", "Mayıs",
                                     "Haziran", "Temmuz", "Ağustos", "Eylül",
                                     "Ekim", "Kasım", "Aralık"]),
            "tarih": self._rand_date(60),
            "muhatap": self._pick([f"{n} {s}" for n in _FIRST_NAMES[:15]
                                   for s in _LAST_NAMES[:10]][:50]),
        }

    # ---- entity generation ----

    def _gen_isci_entities(self, count: int) -> list[EntityDef]:
        entities: list[EntityDef] = []
        for i in range(count):
            eid = f"isci-{i + 1:03d}"
            skills = self.rng.sample(_SKILLS, k=self.rng.randint(1, 3))
            entities.append(EntityDef(
                id=eid,
                entity_type="isci",
                fields={
                    "lokasyon": self._pick(_SERVICE_AREAS),
                    "statik_beceriler": ", ".join(skills),
                },
                free_text=f"{self._pick(_FIRST_NAMES)} {self._pick(_LAST_NAMES)} "
                          f"— {self._pick(_WORK_AREAS)} teknisyeni, "
                          f"{self._rand_int(1, 8)} yıl deneyimli.",
            ))
        return entities

    def _gen_tesis_entities(self, count: int) -> list[EntityDef]:
        entities: list[EntityDef] = []
        for i in range(count):
            eid = f"tesis-{i + 1:03d}"
            entities.append(EntityDef(
                id=eid,
                entity_type="tesis",
                fields={
                    "oda_sayisi": self._rand_int(24, 320),
                    "bolge": self._pick(_REGIONS),
                },
                free_text=f"{self._pick(_TESIS_NAMES)} — {self._pick(_CITIES)} "
                          f"merkezli {self._pick(_TESIS_TIPLERI)}. "
                          f"Açılış: {self._rand_int(2005, 2023)}.",
            ))
        return entities

    def _gen_cari_entities(self, count: int) -> list[EntityDef]:
        entities: list[EntityDef] = []
        for i in range(count):
            eid = f"cari-{i + 1:03d}"
            entities.append(EntityDef(
                id=eid,
                entity_type="cari",
                fields={
                    "firma_adi": self._pick(_COMPANY_NAMES),
                    "telefon": f"05{self._rand_int(30, 55):02d}{self._rand_int(1000000, 9999999):07d}",
                },
                free_text=f"{self._pick(_COMPANY_NAMES)} — "
                          f"{self._pick(_CITIES)} merkezli. "
                          f"Sektör: {self._pick(['İnşaat', 'Tekstil', 'Gıda', 'Otomotiv', 'Lojistik'])}.",
            ))
        return entities

    def _gen_bolge_muduru_entities(self, count: int) -> list[EntityDef]:
        entities: list[EntityDef] = []
        for i in range(count):
            eid = f"bm-{i + 1:03d}"
            entities.append(EntityDef(
                id=eid,
                entity_type="bolge_muduru",
                fields={
                    "sorumlu_tesis_sayisi": self._rand_int(5, 20),
                    "bolge": self._pick(_REGIONS),
                },
                free_text=f"{self._pick(_FIRST_NAMES)} {self._pick(_LAST_NAMES)} "
                          f"— {self._pick(_REGIONS)} bölge müdürü. "
                          f"{self._rand_int(2, 12)} yıl deneyim.",
            ))
        return entities

    # ---- signal generation ----

    def _gen_signals(self, entity_id: str, entity_type: str,
                     count: int) -> list[SignalDef]:
        signals: list[SignalDef] = []
        templates_map = {
            "isci": (_ISCI_POSITIVE_TEMPLATES, _ISCI_NEGATIVE_TEMPLATES,
                     _ISCI_MIXED_NEUTRAL_TEMPLATES),
            "tesis": (_TESIS_POSITIVE_TEMPLATES, _TESIS_NEGATIVE_TEMPLATES,
                      _TESIS_MIXED_NEUTRAL_TEMPLATES),
            "cari": (_CARI_POSITIVE_TEMPLATES, _CARI_NEGATIVE_TEMPLATES,
                     _CARI_MIXED_NEUTRAL_TEMPLATES),
            "bolge_muduru": (_BM_POSITIVE_TEMPLATES, _BM_NEGATIVE_TEMPLATES,
                             _BM_MIXED_NEUTRAL_TEMPLATES),
        }
        pos, neg, mix = templates_map[entity_type]

        # Weighted: ~50% positive, ~30% negative, ~20% mixed/neutral
        for _ in range(count):
            roll = self.rng.random()
            if roll < 0.50:
                template, _ = self._pick(pos)
            elif roll < 0.80:
                template, _ = self._pick(neg)
            else:
                template, _ = self._pick(mix)

            ctx = self._make_context(entity_type)
            text = self._fill(template, ctx)

            self._signal_counter += 1
            signals.append(SignalDef(
                entity_id=entity_id,
                entity_type=entity_type,
                text=self._normalise(text),
                external_id=f"bench-{entity_id}-{self._signal_counter:05d}",
            ))
        return signals

    def _normalise(self, text: str) -> str:
        """Clean up double spaces and trailing newlines."""
        import re
        text = re.sub(r" {2,}", " ", text)
        text = text.strip()
        return text

    # ---- public API ----

    def generate(
        self,
        entity_counts: dict[str, int] | None = None,
        signals_per_entity: dict[str, int] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Generate entities and signals.

        Args:
            entity_counts: how many entities per type.
            signals_per_entity: how many signals per entity of each type.

        Returns
            (entities, signals) where each is a list of dicts suitable for
            serialisation / API calls.
        """
        entity_counts = entity_counts or {
            "isci": 50, "tesis": 50, "cari": 30, "bolge_muduru": 20,
        }
        signals_per_entity = signals_per_entity or {
            "isci": 25, "tesis": 20, "cari": 20, "bolge_muduru": 20,
        }

        generators = {
            "isci": self._gen_isci_entities,
            "tesis": self._gen_tesis_entities,
            "cari": self._gen_cari_entities,
            "bolge_muduru": self._gen_bolge_muduru_entities,
        }

        all_entities: list[EntityDef] = []
        all_signals: list[SignalDef] = []

        for etype, cnt in entity_counts.items():
            if cnt <= 0:
                continue
            gen_fn = generators.get(etype)
            if gen_fn is None:
                print(f"Unknown entity type: {etype}, skipping.")
                continue
            entities = gen_fn(cnt)
            all_entities.extend(entities)

            spc = signals_per_entity.get(etype, 10)
            for ent in entities:
                sigs = self._gen_signals(ent.id, ent.entity_type, spc)
                all_signals.extend(sigs)

        return (
            [_entity_to_dict(e) for e in all_entities],
            [_signal_to_dict(s) for s in all_signals],
        )


def _entity_to_dict(e: EntityDef) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": e.id,
        "entity_type": e.entity_type,
        "fields": e.fields,
    }
    if e.free_text:
        d["free_text"] = e.free_text
    return d


def _signal_to_dict(s: SignalDef) -> dict[str, Any]:
    d: dict[str, Any] = {
        "entity_id": s.entity_id,
        "entity_type": s.entity_type,
        "text": s.text,
    }
    if s.external_id:
        d["external_id"] = s.external_id
    return d
