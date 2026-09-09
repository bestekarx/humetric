# Sözleşme: `src/humetric/context.py` — bağlam ölçümü

**Feature**: `001-context-engineering-cost` · Faz: A1 (temel), A2 (tüketici)

## Amaç

Tek sorumluluk: bir extraction çağrısına girecek metnin token büyüklüğünü, çağrı **öncesinde**,
sağlayıcıya ağ isteği yapmadan tahmin etmek.

## Arayüz

```python
def estimate_tokens(text: str) -> int:
    """Yerel, deterministik tahmin. Aynı girdi her zaman aynı sayıyı döner."""

def measure_extract_inputs(system: str, user: str) -> ContextMeasurement:
    """Extraction çağrısının sistem + user bloklarını ölçer."""

class ContextMeasurement:
    system_tokens: int
    user_tokens: int
    total_tokens: int
```

## Ne YAPMAZ

- Sinyal başına sağlayıcının sayım ucuna (`count_tokens`) ağ çağrısı **yapmaz**. Gerekçe:
  bütçe kapısı çağrıdan önce çalıştığı için bu maliyet her sinyale biner; ek gecikme ve hata
  yüzeyi kabul edilemez (research R5).
- OpenAI tokenizer'ı (`tiktoken` vb.) **kullanmaz** — farklı bir sağlayıcının tokenizer'ıdır ve
  Türkçe/kod metinde belirgin şekilde eksik sayar.
- Metni kırpmaz, değiştirmez — salt okunur ölçüm.

## Kalibrasyon (çevrimdışı, ayrı araç)

`scripts/calibrate_token_estimate.py`, örnek metinleri hem `estimate_tokens()` ile hem
yapılandırılmış sağlayıcının gerçek sayım ucuyla ölçer ve **sapma yüzdesiyle önerilen katsayıyı
rapor eder**. Araç hiçbir kaynak dosyayı düzenlemez: katsayıyı `config.py`'nin varsayılanına
ya da ortam değişkenine **insan** yazar. Bu araç **runtime'da çalışmaz**; geliştirici/operatör
elle tetikler.

## Tüketiciler

1. **A2 — bütçe kapısı**: extraction çağrısından önce `measure_extract_inputs()` çağrılır;
   `total_tokens` yapılandırılmış bütçeyle karşılaştırılır (bkz.
   `contracts/trace-budget-flag.md`).
2. **A1 — iz kaydı**: ölçülen `total_tokens`, `trace_data.measured_input_tokens` olarak
   yazılır (bütçe aşılsın aşılmasın).

## Kabul kriterleri

1. Aynı `(system, user)` çifti her çağrıda aynı sayıyı döner (deterministik).
2. Fonksiyon çağrısı sinyal işleme gecikmesine ölçülebilir bir ağ turu eklemez.
3. `scripts/calibrate_token_estimate.py` bir örnek küme üzerinde çalıştırıldığında sapma
   yüzdesini ve önerilen katsayıyı raporlar; hiçbir dosyayı kendisi değiştirmez ve sapma
   raporlanmadan katsayı güncellenmez.

## Anayasa denetimi

- **İlke II**: Bu modül hiçbir sağlayıcı SDK'sı import etmez (kalibrasyon aracı hariç, o da
  yalnızca yapılandırılmış sağlayıcı üzerinden — marka adı geçmez).
- **İlke VI**: Motora ait, siteye ait değil; site kendi karşılığını C3'te ayrı kurar.
