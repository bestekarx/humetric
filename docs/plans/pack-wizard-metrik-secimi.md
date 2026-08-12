# Pack Wizard — üretilen metrikleri inceleme ve seçme adımı

> Durum: **taslak, uygulanmadı**. Bu görev **humetric-site** (frontend/backend
> ayrı repo) tarafında yapılacak — bu repo (`humetric`) backend-only, wizard
> UI'ı barındırmaz. Backend tarafında gereken tek şey (max 7 metrik sınırı)
> zaten uygulandı, aşağıda not edildi.

## Neden

`POST /v1/packs/wizard` (agents/wizard.py, `claude-haiku-4-5`) bir doğal dil
açıklamasından pack YAML'ı üretiyor. Kullanıcı şu anki akışta üretilen tüm
metrikleri görmeden, sebeplerini bilmeden pack'i doğrudan kaydediyor. İki
sorun:

1. **Kullanıcı neyin ölçüldüğünü, neden ölçüldüğünü görmüyor.** Metrik
   isimleri ve prompt'ları teknik/soyut kalabiliyor (örn. bu repoda test
   ettiğimiz YouTube pack'inde `credibility_backlash`, `novelty_transfer` gibi
   isimler — kullanıcı "bu ne işime yarar" diye sormadan kabul ediyor).
2. **Fazla metrik = motor tarafında sessiz arıza.** `humetric` backend'inde
   extraction LLM çağrısı her metrik için `value + confidence + reasoning +
   source_span` içeren tek bir zorunlu (forced tool-use) JSON nesnesi üretmek
   zorunda, `HUMETRIC_MAX_TOKENS` (varsayılan 2048) ile sınırlı bir çıktıda.
   ~7-8 metrikten sonra bu çıktı kesiliyor ve **hatasız şekilde** `metrics: []`
   dönüyor — kullanıcı "gönderdim ama boş" şaşkınlığına düşüyor (bu proje
   üzerinde canlı olarak yaşandı, bkz. commit geçmişi / bu konuşma). Backend
   artık `PackDefinition.metrics` alanına **create/update anında** en fazla 7
   metrik sınırı koyuyor (`src/humetric/schema.py:PackDefinition._limit_metric_count`,
   `HUMETRIC_MAX_METRICS_PER_PACK` env ile ayarlanabilir). Wizard'ın kendisi
   bu sınırı **aşan sayıda metrik üretebilir** — sorun değil, çünkü nihai
   `POST /v1/packs` çağrısı sadece kullanıcının seçtiği ≤7 metrikle yapılacak.

## Akış (mevcut mockup'a göre: `docs/mockups/5-paket.html` tarzı wizard adımları)

```
[Açıklama / DB Şeması / Örnek Veri / Ekran Görüntüsü] → "Pack üret"
                    ↓
        POST /v1/packs/wizard  (mevcut, degismiyor)
                    ↓
        YENİ ADIM: "İncele" — üretilen TÜM metrikleri listele
                    ↓
        Kullanıcı max 7 tanesini işaretler/kaldırır
                    ↓
        Onayla → POST /v1/packs  (SADECE seçili metriklerle, trimlenmiş YAML)
```

### "İncele" adımı — UI

Var olan sekme çubuğuna (Açıklama / DB Şeması / Örnek Veri / Ekran Görüntüsü)
bir beşinci sekme eklenebilir: **"İncele"** — ya da mevcut "Pack üret"
butonundan sonra otomatik olarak bu adıma geçilir (akış: Tanımla → İncele →
Onayla, tek adım daha).

Her metrik için gösterilecekler:

| Alan | Kaynak | Örnek |
|---|---|---|
| Checkbox (varsayılan: ilk 7 işaretli, `primary_metrics`/önem sırasına göre) | — | ☑ |
| Etiket | `metric.label` | "İzleyici Duygu Dengesi" |
| **Neden bu?** — 1 cümlelik gerekçe | wizard'dan **yeni** bir alan istenmeli (aşağıda) | "Yorumlardaki olumlu/olumsuz oranını izler, içerik kalitesinin doğrudan göstergesi." |
| Kısaltılmış prompt önizlemesi (genişletilebilir "Detay" linki) | `metric.prompt` (ilk ~120 karakter) | "Bölümün tüm yorumlarını tek tek olumlu/nötr/olumsuz olarak etiketle..." |
| Yön rozeti | `metric.direction` | ↑ iyi / ↓ iyi |
| Hassas veri rozeti (varsa) | `metric.sensitive` + `kvkk.sensitive_metrics` | 🔒 KVKK onayı gerekir |

Seçim sayacı: **"4 / 7 metrik seçildi"** — 7'yi aşan işaretleme denemesi
engellenir (ya en eski seçim otomatik kaldırılır ya da buton disable + tooltip
"En fazla 7 metrik seçebilirsiniz — motor bunun üzerinde sessizce boş sonuç
döndürüyor").

Kaldırılan bir metrik anında hem UI listesinden hem de "Onayla" ile gidecek
YAML'dan (`definition.metrics`, `definition.display.groups[].metrics`,
`definition.display.primary_metrics`, `definition.kvkk.sensitive_metrics`)
çıkarılmalı — bu dört yerin hepsi tutarlı kalmalı, aksi halde `display.groups`
var olmayan bir metric_key'e referans verip UI'da kırık sekme oluşturur.

### Backend tarafında gereken (küçük) değişiklik

`agents/wizard.py`'nin ürettiği YAML şemasına (`WizardPackDraft` ya da
eşdeğeri — kontrol edin) her metrik için bir **`rationale`** (veya
`why: str`) alanı eklenmesi gerekebilir, sadece wizard'ın kendi çıktı
şemasında — nihai `PackMetricDef`'e (schema.py) sızmasına gerek yok, çünkü bu
alan sadece "İncele" ekranında gösterilip sonra atılacak. Wizard'ın sistem
promptuna ("prompts/wizard-default.md" veya eşdeğeri) tek satır ekleme
yeterli: *"Her metrik için 1 cümlelik `rationale` alanı da üret: bu metrik
neyi, neden ölçüyor."*

Bu **tek** backend değişikliği — geri kalanı tamamen humetric-site'ta.

## humetric-site tarafında yapılacaklar (özet checklist)

- [ ] Wizard'ın döndürdüğü ham metrik listesini state'te tut (henüz
      `POST /v1/packs`'e gitmeden).
- [ ] "İncele" adımını render et (tablo/kart listesi, checkbox, max 7 kilidi).
- [ ] Kaldırma/ekleme, YAML'ın 4 bölümünü (metrics, display.groups,
      display.primary_metrics, kvkk.sensitive_metrics) senkron tutacak şekilde
      state'ten YAML'ı yeniden üretsin (ya client-side YAML manipülasyonu ya
      da onay anında backend'e "seçili key listesi" gönderip trim işlemini
      backend'in yapması — ikincisi daha güvenli, YAML syntax hatası riski
      yok).
- [ ] Onayla → `POST /v1/packs` (mevcut endpoint, değişmiyor).
- [ ] 7'den fazla metrik göndermeye çalışırsa zaten backend 422 dönecek
      (`schema.py` validator) — bunu kullanıcıya düzgün bir hata mesajıyla
      göster (validator mesajı zaten insan-okunur: *"A pack may define at
      most 7 metrics..."*).

## Açık sorular (bu doküman ilerlerken netleştirilecek)

- Wizard zaten kaç metrik üretiyor tipik olarak? (Bu repodaki YouTube pack
  örneği 15 ürettmiş — muhtemelen wizard bilinçli olarak "bol öneri, kullanıcı
  seçsin" mantığıyla çalışıyor, bu doğrulanmalı.)
- Kullanıcı 7'den az metrik seçerse sorun yok (validator `en fazla`, `en az`
  değil) — ama UI'da "en az 1 metrik seçmelisin" uyarısı olmalı.
