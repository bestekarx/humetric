# Sözleşme: Site — sihirbaz girdi sınırı + çok turlu caching

**Feature**: `001-context-engineering-cost` · Fazlar: C1, C2, C3

> **Bu sözleşme `../humetric-site` projesinde uygulanır.** Bu depoya (`humetric`) hiçbir
> dosya eklenmez ya da değiştirilmez (Anayasa İlke VI). Buraya yazılma nedeni, iki repo için
> ortak referans olacak tek bir bağlam sözleşmesi istenmiş olmasıdır (`docs/architecture/
context-engineering.md`, Çıktı 1).

## C1 — Sihirbaz girdi sınırı

**Dosya**: `backend/src/mcpServer/tools.ts`

Bugün `humetric_pack_wizard_start`'ın `text`, `db_schema` ve `sample_data` parametrelerinde
`.max()` yok (`tools.ts:139-143`). `sample_data` daha sonra promptta 3.000 karaktere
kırpılıyor (`packWizardPrompt.ts`), ama `text`/`db_schema` olduğu gibi giriyor ve ajanın
**her turunda** yeniden gönderiliyor.

**Değişiklik**: Her üç alana Zod `.max()` sınırı eklenir. Referans kalıp aynı dosyadaki
signal chat'in `text` alanı: `z.string().min(10).max(MAX_SIGNAL_TEXT_CHARS)`
(`MAX_SIGNAL_TEXT_CHARS = 60000`, `tools.ts:58,251`). Sihirbaz alanları için **somut sınırlar**
(spec FR-016; DDL ve serbest metin farklı büyüklük profillerine sahip olduğu için üç ayrı
sabit):

| Alan | Sınır | Gerekçe |
|---|---|---|
| `text` | **60.000** karakter | Sitedeki mevcut `MAX_SIGNAL_TEXT_CHARS` kalıbı aynen benimsenir. |
| `db_schema` | **40.000** karakter | Büyük bir DDL dökümünü kabul eder, sınırsızlığı bitirir. |
| `sample_data` | **20.000** karakter | `packWizardPrompt.ts` zaten 3.000'e kırpıyor; 20.000 fazlasıyla yeterli. |

Bu değerler ölçüm sonrası ayarlanabilir; ayarlanana kadar **bağlayıcıdır** (bkz. spec FR-016).

**Zorunlu davranış**: Sınır **doğrulama katmanında** (Zod şeması) uygulanır — sınır aşıldığında
istek şema hatasıyla reddedilir, hiçbir LLM çağrısı yapılmaz ve `billing.ts` üzerinden hiçbir
kredi düşülmez. Promptta sessiz kırpma **yapılmaz**.

**Kabul kriterleri**:
1. 60.000 karakteri aşan `text` ya da 40.000 karakteri aşan `db_schema` gönderildiğinde istek
   Zod hatasıyla reddedilir.
2. Reddedilen istekte `charge`/kredi düşümü tetiklenmez (mevcut `tools.charge.test.ts` kalıbı
   örnek alınır).
3. Sınır içi girdi bugünkü gibi normal çalışır.

## C2 — Çok turlu caching

**Dosyalar**: `backend/src/agentic/loop.ts`, `backend/src/promptLoader.ts`,
`backend/src/agentic/signalGraph/nodes.ts`

Site'ın varsayılan ajan modelinin cache eşiği motorunkinden belirgin biçimde düşük ve site'ın
prefix'i (statik sistem promptu + dossier + büyüyen konuşma geçmişi) zaten büyük — yani
işaretleme koymak tek başına yeterli.

**Önkoşul (zorunlu sıra)**: `promptLoader.ts:32-37`'deki `injectTenantMemory()`, kiracı
gerçeklerini sistem promptunun **en önüne** ekliyor (`signalGraph/nodes.ts:62`). Cache prefix
eşleşmesi olduğundan bu, prefix'in ilk baytlarını her kiracıda ve kiracı belleği her
değiştiğinde farklılaştırır — breakpoint konsa bile hiçbir şey kazandırmaz. **Kiracı belleği
statik prefix'ten sonraya taşınmadan** breakpoint eklemenin bir faydası yoktur.

**Değişiklik**:
1. Kiracı belleği enjeksiyonu statik sistem prefix'inin **sonrasına** taşınır.
2. Statik sistem prefix'inin (kiracı belleğinden bağımsız kalan kısmı) son bloğuna açık
   cache breakpoint'i konur.
3. Artan konuşma kuyruğunda, en son eklenen turun son içerik bloğuna breakpoint konur — geçmiş
   büyüdükçe cache'ten okunan miktar da büyür.

**Kabul kriterleri**:
1. Kiracı belleği taşındıktan sonra, statik sistem prefix'i kiracıdan kiracıya bayt-özdeştir.
2. Aynı oturumun ikinci turunda cache okuma token'ı sıfırdan büyüktür.
3. Kiracı belleği taşındıktan sonra cache okuma oranı **düşmez** (regresyon testi).

## C3 — Site ölçümü ve mutabakat

**Dosyalar**: `backend/src/agentic/tokenUsage.ts`, `backend/schema.sql`,
`backend/src/agent/anthropicClient.ts`

`tokenUsage.ts:60-64`'teki `anthropicUsage()` bugün cache yazma ve cache okuma token'larını
`input`'a katlıyor — bu, C2'nin işe yarayıp yaramadığını ölçülemez kılıyor.

**Değişiklik**:
1. `llm_token_usage` tablosuna (`backend/schema.sql:179-189`) cache okuma ve cache yazma için
   ayrı sütunlar eklenir; katlama kaldırılır.
2. `anthropicClient.ts`'e her sağlayıcı çağrısı için açık zaman aşımı eklenir (bugün yalnızca
   SDK varsayılanı var).

**Bilinen ve bu turda kapatılmayan açık**: Kredi, MCP aracı çağrısı başına düşüyor
(`billing.ts:51-81`), LLM çağrısı başına değil; iade yolu yok; panel (JWT) rotaları hiç
ücretlendirmiyor. BYOK'ta gerçek token maliyeti kiracının kendi faturasına gittiği için bugün
tolere edilebilir. Bu sınır `docs/architecture/context-engineering.md`'de **açıkça yazılır**
ki gelecekte platform-anahtarlı bir akış eklenirse bu açık bilinerek eklensin.

**Kabul kriterleri**:
1. Bir ajan oturumunun ikinci turunda cache token'ı, `llm_token_usage`'da girdi token'ından
   ayrı bir sütunda görünür.
2. Zaman aşımına uğrayan bir sağlayıcı çağrısı, yapılandırılan süre içinde hata ile sonlanır.

## Kasıtlı tekrar — C4 (netleştirme, karar değil)

Site'ın Pack Wizard'ı motorun `wizard.generate_pack()`'ini çağırmaz; bu **korunur** —
pack üreten deneyim site'a, YAML doğrulaması motora aittir. Bu turda netleştirilen (karar
verilmeyen) iki nokta: (a) wizard/critique promptları site'da dosyaya çıkarılmalı, motordaki
`prompts/wizard-system.md` kalıbı örnek alınmalı; (b) motorun kendi `wizard.generate_pack()`'i
hâlâ kullanılıyor mu, tespit edilmeli.
