# Sözleşme: `/v1/query` koşullu yeniden sıralama

**Feature**: `001-context-engineering-cost` · Faz: B2

## Bugünkü davranış

`api.py:1637-1647` her `/v1/query` çağrısında `ranker.rank_entities()`'i **koşulsuz** çalıştırır
— aday listesi boş değilse her zaman bir LLM çağrısı yapılır. `QueryRequest`
(`schema.py:332-340`) bunu kapatacak bir alan taşımaz.

## Hedef davranış

`QueryRequest`'e varsayılanı **`True`** olan bir yeniden sıralama anahtarı eklenir.

- `True` (varsayılan, parametre verilmediğinde): bugünkü davranış — LLM sıralayıcı çalışır.
- `False`: LLM çağrısı **hiç yapılmaz**. Sonuçlar `Store.hybrid_search_entities()`'in kendi
  skoruna göre döner (hibrit arama zaten kendi sıralamasını üretiyor).

## Yanıt şekli

`QueryResponse` ve `RankedResult` **değişmez**. `include_reasoning=true` ile birlikte
yeniden sıralama kapatılırsa `reasoning` alanı doğal olarak boş/`None` kalır — bu bir hata
değil, LLM çalışmadığının beklenen sonucudur.

## Geriye dönük uyumluluk

Parametre gönderilmeyen her mevcut istek, varsayılan `True` sayesinde **birebir bugünkü**
davranışı görür. Bu, `HM-REAS-05` (sessiz davranış değişikliği) ihlalini önlemek için
zorunludur.

## Kapsam sınırı

Aday sayısına göre otomatik karar (ör. "5'ten az adaysa LLM'e gitme") bu turda **eklenmez**.
Eşik değeri ölçüm olmadan keyfî olur. Açık anahtar şimdi gelir; otomatik eşik, ölçüm
sonrasına bırakılan ayrı bir karardır.

## Kabul kriterleri

1. Anahtar `false` gönderilen bir sorguda, o istek için `llm_call_record`'a hiçbir satır
   eklenmez.
2. Anahtar gönderilmeyen bir sorgu, bu sözleşme öncesiyle **birebir aynı** yanıtı üretir.
3. Anahtar `false` iken sonuç listesi boş değildir (hibrit arama sonucu döner) — hata değil.

## Anayasa denetimi

- **İlke II**: Ranker çağrısının kendisi değişmez; yalnızca çağrılıp çağrılmayacağı koşullu
  hâle gelir. `structured_call_multi()` üzerinden dispatch bugünkü gibi kalır.
- **HM-REAS-05**: Varsayılanın `True` olması bu riski doğrudan kapatır.
