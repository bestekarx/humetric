# Sözleşme: `llm_call_record` token ayrımı

**Feature**: `001-context-engineering-cost` · Faz: A1

## Kapsam

`services/usage_service.py:90` (`record_llm_tokens()`) → `:65` (`_insert_llm_call_record()`) yazma yolu ve
onu çağıran üç sağlayıcı dalı (`agents/base.py` Anthropic, `agents/multi_llm.py` OpenAI/
DeepSeek ve Google).

## İmza değişikliği

```python
async def record_llm_tokens(
    tenant_id: int,
    count: int,                     # DEĞİŞMEZ — toplam, token_count'a yazılır
    *,
    signal_id: str | None = None,
    pack_key: str | None = None,
    pack_version: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,        # YENİ
    output_tokens: int | None = None,       # YENİ
    cache_read_tokens: int | None = None,   # YENİ
    cache_write_tokens: int | None = None,  # YENİ
) -> None:
```

`count` parametresi ve `token_count` kolonunun anlamı **değişmez** — geriye dönük uyumluluk
budur. Yeni dört parametre eklenir, çıkarılan hiçbir parametre yoktur.

## Sağlayıcı başına alan eşlemesi

| Sağlayıcı | Kaynak | Durum |
|---|---|---|
| Anthropic | `resp.usage.input_tokens`, `.output_tokens`, `.cache_read_input_tokens`, `.cache_creation_input_tokens` | **Doğrulandı** — Anthropic SDK bu dört alanı `Usage` nesnesinde döndürür. |
| DeepSeek | `resp.usage.prompt_tokens`, `.completion_tokens`, `.prompt_cache_hit_tokens`, `.prompt_cache_miss_tokens` | **Doğrulandı (2026-09-08, canlı ölçüm).** `prompt_cache_hit_tokens` → `cache_read_tokens`. **Cache yazma sayısı raporlanmıyor** → `cache_write_tokens` her zaman `None`. `prompt_tokens` cache'lenen kısmı **içerir** (hit + miss = prompt_tokens), bu yüzden `input_tokens`'a olduğu gibi yazılır ve hit ondan çıkarılmaz. |
| OpenAI | `resp.usage.prompt_tokens`, `.completion_tokens`, `.prompt_tokens_details.cached_tokens` | **Doğrulanacak.** DeepSeek ile aynı SDK dalını (`_call_openai`) paylaşır ama alan adları aynı değildir; implementasyon öncesi kontrol edilir. Alan yoksa `None`. |
| Google | `resp.usage_metadata.prompt_token_count`, `.candidates_token_count`, `.cached_content_token_count` | **Doğrulandı (2026-09-09, canlı ölçüm).** `cached_content_token_count` → `cache_read_tokens`. **Bu alan her zaman mevcuttur ve cache isabeti yokken `0` döner** — yani buradaki `0` gerçek bir "isabet yok" değeridir, "raporlanmıyor" değil; olduğu gibi yazılır (aşağıdaki nota bakınız). Cache yazma alanı yok → `cache_write_tokens` `None`. |

## Kural: yoksa `None`, asla sıfır ya da tahmin

> **`0` ile `NULL` ayrımı — Google ölçümüyle netleşti.** Kural "sıfır yazma" değil,
> **"uydurma"**. Sağlayıcı alanı *döndürüyorsa* değeri olduğu gibi yazılır, `0` olsa bile:
> Google'da `cached_content_token_count` her yanıtta mevcuttur ve isabet yokken `0`'dır, bu
> anlamlı bir ölçümdür. `NULL` yalnızca sağlayıcı alanı **hiç döndürmediğinde** yazılır
> (DeepSeek'te cache yazma sayısı, Google'da cache yazma sayısı). Uygulama testi:
> alan yanıtta var mı — değeri kaç değil.

Bir sağlayıcı bir alanı raporlamıyorsa o alan `NULL` olarak kaydedilir. `0` yazmak "cache
çalışmadı" ile "sağlayıcı raporlamıyor"u ayırt edilemez kılar. `input_tokens` +
`output_tokens` her zaman sağlayıcının döndürdüğü değerdir; `token_count`'tan **türetilmez** —
ikisi ayrı ayrı sağlayıcı yanıtından okunur (tutarsızlık varsa bu, sağlayıcı davranışına dair
gerçek bir sinyal olur, gizlenmez).

## Kabul kriterleri

1. Bir sinyal doğrulanmış bir sağlayıcıyla işlendiğinde, `llm_call_record` satırında
   `input_tokens`, `output_tokens` ve `cache_read_tokens` **doldurulmuş** gelir. DeepSeek
   yolunda `cache_write_tokens` `NULL` kalır ve bu **beklenen** sonuçtur — sağlayıcı o sayıyı
   hiç raporlamaz (FR-002'nin tam olarak koruduğu durum).
2. `cache_read_tokens > 0` olan bir satır, aynı prefix'in daha önce yazıldığını kanıtlar (bkz.
   `contracts/prompt-composition.md` § cache kanıtı).
2b. Google yolunda `cache_read_tokens` `0` olarak kaydedilir ve bu **doğru** sonuçtur —
   ölçüm, o çağrıda cache isabeti olmadığını söyler. `NULL` yazmak bilgiyi kaybettirirdi.
3. Bu sözleşme uygulandıktan sonra üretilen metrik değerleri, öncesiyle **birebir aynıdır** —
   bu sözleşme yalnızca kayıt genişletir, çıkarımı değiştirmez.
4. Mevcut `store.py:495` (pack kullanım raporu) `token_count` üzerinden okumaya devam eder ve
   değişmeden çalışır.

## Anayasa denetimi

- **İlke I**: Yeni `tenant_id` tablosu yok; mevcut RLS'li tabloya nullable kolon. Yeni politika
  gerekmez — bkz. migration docstring'i ve `data-model.md` §1.
- **İlke II**: Sağlayıcı alan adları `multi_llm.py`'nin kendi dalları içinde çözülür; ortak bir
  soyutlamaya zorlanmaz. Marka model adı bu dosyada geçmez.
- **İlke IV**: Bu sözleşme hiçbir sunucu adresi, anahtar veya kişisel yol içermez.
