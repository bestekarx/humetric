# HuMetric'i yapay zeka asistanınıza bağlayın

HuMetric MCP sunucusu, Claude'un — ya da MCP konuşan herhangi bir asistanın —
HuMetric verinizle doğrudan çalışmasını sağlar: sinyal gönderme, varlık
metriklerini okuma, bir metriğin neden değiştiğini sorma, Metric Pack yönetme,
bekleyen incelemeleri onaylama. Panelle sohbet penceresi arasında kopyala-yapıştır
yapmaya gerek kalmaz.

Kurulum iki adım: **sunucuyu kurun**, sonra **istemcinizi ona yönlendirin**.
Yaklaşık beş dakika sürer.

> 🇬🇧 English version: [MCP Setup](/mcp-setup)

## Gerekenler

| | |
|---|---|
| **HuMetric API anahtarı** | `hm_live_` ile başlar. Panelde **Ayarlar → API Anahtarları** bölümünden ya da `POST /v1/api-keys` ile oluşturun (bkz. [Authentication](/guide/authentication)). |
| **API adresiniz** | Bulut hizmeti için `https://api.gethumetric.com`, HuMetric'i kendiniz çalıştırıyorsanız `http://localhost:8002`. |
| **uv** | Kurulum aracı. Yoksa: `curl -LsSf https://astral.sh/uv/install.sh \| sh` (macOS/Linux) veya `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` (Windows). |

API anahtarını bir sonraki adım için güvenli bir yerde tutun — yalnızca
oluşturulduğu anda bir kez gösterilir.

## 1. Adım — Sunucuyu kurun

Depoyu klonlamanıza veya Python ortamı hazırlamanıza gerek yok. Tek komut:

```bash
uvx --from git+https://github.com/bestekarx/humetric.git humetric-mcp --help
```

Bu komut sunucuyu indirir, izole bir ortama kurar ve kullanım bilgisini yazdırır.
Birkaç saniye sürer. Kullanım metnini gördüyseniz hazırsınız.

::: tip Neden `uvx`?
`uvx`, sunucuyu sistem Python'unuza dokunmadan ve size yönetmeniz gereken bir
sanal ortam bırakmadan çalıştırır. MCP sunucusu ince bir HTTP istemcisi — yaklaşık
30 küçük paket kurulur, veritabanı sürücüsü inmez.
:::

## 2. Adım — İstemcinizi bağlayın

Kullandığınız asistanı seçin.

### Claude Code

Tek komut, herhangi bir dizinde çalıştırabilirsiniz:

```bash
claude mcp add humetric --scope user \
  --env HUMETRIC_MCP_API_KEY=hm_live_anahtariniz \
  --env HUMETRIC_BASE_URL=https://api.gethumetric.com \
  -- uvx --from git+https://github.com/bestekarx/humetric.git humetric-mcp
```

`--scope user` HuMetric'i tüm projelerde kullanılabilir yapar ve API anahtarınızı
hiçbir depoya sokmaz. Yalnızca bulunduğunuz projede istiyorsanız `--scope local`
kullanın.

`claude mcp list` ile doğrulayın — `humetric: ... ✔ Connected` görmelisiniz.

### Claude Desktop

Yapılandırma dosyanızı düzenleyin:

- **macOS** — `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows** — `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "humetric": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/bestekarx/humetric.git",
        "humetric-mcp"
      ],
      "env": {
        "HUMETRIC_MCP_API_KEY": "hm_live_anahtariniz",
        "HUMETRIC_BASE_URL": "https://api.gethumetric.com"
      }
    }
  }
}
```

Claude Desktop'ı tamamen kapatıp yeniden açın (pencereyi kapatmak yetmez,
uygulamadan çıkın). HuMetric ardından araçlar menüsünde görünür.

::: warning Claude Desktop `uvx`'i bulamazsa
Claude Desktop sunucuları kabuğunuzun `PATH`'i olmadan başlatır; bu yüzden düz
`uvx` çözümlenmeyebilir. `which uvx` (macOS/Linux) veya `where.exe uvx` (Windows)
çalıştırıp çıkan **mutlak yolu** `"command"` alanına yazın — örneğin
`/Users/kullanici/.local/bin/uvx`. Kurulumda en sık karşılaşılan sorun budur.
:::

### Cursor

Projenizde `.cursor/mcp.json` (veya tüm projeler için `~/.cursor/mcp.json`)
oluşturup yukarıdaki Claude Desktop `mcpServers` bloğunun aynısını yazın.

### VS Code (GitHub Copilot)

`.vscode/mcp.json` oluşturun:

```json
{
  "servers": {
    "humetric": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/bestekarx/humetric.git",
        "humetric-mcp"
      ],
      "env": {
        "HUMETRIC_MCP_API_KEY": "hm_live_anahtariniz",
        "HUMETRIC_BASE_URL": "https://api.gethumetric.com"
      }
    }
  }
}
```

Ardından Copilot Chat'i **Agent** modunda açıp HuMetric araçlarını etkinleştirin.

::: danger Anahtarınızı commit etmeyin
`.cursor/mcp.json` ve `.vscode/mcp.json` deponuzun içinde yer alır. Bunları
`.gitignore`'a ekleyin veya kullanıcı seviyesinde yapılandırma tercih edin.
:::

## 3. Adım — Çalıştığını doğrulayın

Asistanınıza sorun:

> humetric_health tool'unu kullan ve durumu söyle.

Sağlıklı bir yanıt API durumunu ve worker'ın çalışıp çalışmadığını bildirir.
Bunun yerine eksik veya reddedilmiş API anahtarıyla ilgili bir mesaj alıyorsanız
[Sorun giderme](#sorun-giderme) bölümüne geçin.

Sonra gerçek bir şey deneyin:

> Varlıklarımı listele, sonra ilkinin en yüksek puanlı metriğini açıkla.

## Asistanınız artık neler yapabilir

25 tool, alanlara göre:

| Alan | Kapsamı |
|---|---|
| **Sinyaller** | Ham metin sinyali gönderme, işlenme durumunu izleme, tam çıkarım izini okuma |
| **Varlıklar ve metrikler** | Varlık oluşturma/güncelleme, güncel metrikleri okuma, bir metriği açıklama, geçmişini okuma |
| **Sorgulama** | Hibrit erişimle varlıklar arasında doğal dil araması |
| **Metric Pack** | Pack tanımlarını listeleme, okuma, oluşturma ve güncelleme |
| **İnsan onayı** | Onay bekleyen metrikleri listeleme, onaylama veya reddetme |
| **Rıza (KVKK/GDPR)** | Rıza kapsamlarını okuma, verme ve geri çekme |
| **Hesap** | Panel özeti, kullanım raporu, çağrı geçmişi, denetim kaydı, sağlık kontrolü |

Üç hazır iş akışı MCP prompt'u olarak gelir: `analyze_entity`,
`investigate_signal`, `draft_metric_pack`.

Bilerek dışarıda bırakılanlar: hesap açma, API anahtarı oluşturma, BYOK sağlayıcı
sırları ve faturalandırma. Bir asistanın kendi kimlik bilgilerini üretebilmesi ya
da para hareketi başlatabilmesi doğru değil. Gerekçelerin tamamı için
[MCP referansı](/mcp).

::: tip Sinyaller asenkron işlenir
Sinyal gönderdiğinizde dönen yanıt `status: "received"` olur, hazır metrikler
değil. Sunucu asistanınıza, metrikleri okumadan önce `humetric_get_signal` ile
durum `completed` olana kadar beklemesini söyler — kısa bir gecikme normaldir.
:::

::: info Çağrılarınız meta veri olarak kaydedilir
MCP sunucusunun attığı her istek, onu yapan API anahtarına yazılır: hangi tool,
hangi uç, HTTP durumu ve süresi. Sinyal metni, varlık içeriği ve tool
argümanları **kaydedilmez**. Kendi etkinliğinizi görmek için asistanınızdan
`humetric_call_history` isteyin — örneğin "bu ay en çok hangi tool'u
kullandım?".
:::

## Yapılandırma değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `HUMETRIC_MCP_API_KEY` | — | **Zorunlu.** `hm_live_...` API anahtarınız |
| `HUMETRIC_BASE_URL` | `http://localhost:8002` | HuMetric API adresi |
| `HUMETRIC_MCP_TIMEOUT_S` | `30` | İstek başına zaman aşımı (saniye) |
| `HUMETRIC_MCP_MAX_ITEMS` | `50` | Yanıtta kırpılmadan önceki en fazla liste öğesi |
| `HUMETRIC_MCP_LOG_LEVEL` | `INFO` | Log seviyesi; loglar stderr'e yazar |

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| "Server failed to start" / "server disconnected" | Neredeyse her zaman `uvx`'in bulunamamasıdır. `which uvx` çıktısındaki mutlak yolu `"command"` alanına yazın. |
| Tüm tool'lar eksik API anahtarı diyor | `HUMETRIC_MCP_API_KEY` sunucuya ulaşmıyor. `env` bloğunun içinde olduğunu ve istemciyi tamamen yeniden başlattığınızı kontrol edin. |
| `401 invalid_api_key` | Anahtar yanlış veya iptal edilmiş. Panelden yeni bir tane oluşturun. |
| Tool'lar zaman aşımına uğruyor | `HUMETRIC_BASE_URL` erişilebilir değil. Adresi, kendi sunucunuzu çalıştırıyorsanız API'nin ayakta olduğunu doğrulayın. |
| `502 llm_auth_failed` | Sizden değil sunucudan kaynaklanır: HuMetric kurulumunun LLM sağlayıcı anahtarı geçersiz. Yöneticinize bildirin. |
| Sinyaller `queued`de kalıyor | Arka plan worker'ı çalışmıyor. `humetric_health` ile doğrulayıp worker'ı yeniden başlatın. |
| `ModuleNotFoundError: mcp.server.fastmcp` | `mcp` 2.x'e izin veren eski bir sürümdesiniz. Son sürümü çekip yeniden kurun — bağımlılık artık 1.x serisine sabitlendi. |

## Kendi sunucunuzda çalıştırma ve uzak erişim

HuMetric'i kendiniz mi çalıştırıyorsunuz? `HUMETRIC_BASE_URL`'i kendi
kurulumunuza yönlendirin — bu sayfadaki her şey aynı kalır.

Her kullanıcıda ayrı ayrı çalıştırmak yerine tek bir MCP ucunu ağ üzerinden
birden fazla istemciye sunmak için:

```bash
humetric-mcp --transport sse --port 8765
humetric-mcp --transport streamable-http --port 8765
```

::: warning
API anahtarı sunucu sürecinin ortamından okunur; yani ağa açılan bir örnek,
bağlanan her istemci için o tek anahtar adına işlem yapar. Tenant başına bir
tane çalıştırın ve güvenilmeyen bir ağa asla açmayın.
:::

Tool listesinin tamamı, kapsam kararları ve tasarım notları için
[MCP referansı](/mcp).
