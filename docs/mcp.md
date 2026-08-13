# HuMetric MCP Sunucusu

HuMetric'i Claude'a bağlar: Claude Desktop, Claude Code veya MCP konuşan
herhangi bir istemci, HuMetric API'sini doğrudan kullanabilir.

## Kurulum

```bash
pip install -e .              # mcp>=1.0 bağımlılığı pyproject'te
```

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "humetric": {
      "command": "/mutlak/yol/humetric/.venv/bin/python",
      "args": ["/mutlak/yol/humetric/mcp_server.py"],
      "env": {
        "HUMETRIC_MCP_API_KEY": "hm_live_...",
        "HUMETRIC_BASE_URL": "http://localhost:8002"
      }
    }
  }
}
```

**Mutlak yol kullanın.** Claude Desktop komutu kendi çalışma dizininden
başlatır; göreli yol sessizce "server failed to start" ile sonuçlanır.

### Claude Code

```bash
claude mcp add humetric \
  --env HUMETRIC_MCP_API_KEY=hm_live_... \
  --env HUMETRIC_BASE_URL=http://localhost:8002 \
  -- /mutlak/yol/humetric/.venv/bin/python /mutlak/yol/humetric/mcp_server.py
```

### Uzak host (SSE / streamable-http)

```bash
python mcp_server.py --transport sse --port 8765
python mcp_server.py --transport streamable-http --port 8765
```

## Ortam değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `HUMETRIC_MCP_API_KEY` | — | **Zorunlu.** `hm_live_...` API anahtarı |
| `HUMETRIC_BASE_URL` | `http://localhost:8002` | HuMetric API adresi |
| `HUMETRIC_MCP_TIMEOUT_S` | `30` | İstek zaman aşımı |
| `HUMETRIC_MCP_MAX_ITEMS` | `50` | Yanıtlarda listelerin kırpılma sınırı |
| `HUMETRIC_MCP_LOG_LEVEL` | `INFO` | Log seviyesi (stderr'e yazar) |

## Tool'lar (24)

**Sinyaller** — `humetric_ingest_signal`, `humetric_get_signal`,
`humetric_get_signal_trace`, `humetric_list_entity_signals`

**Varlıklar ve metrikler** — `humetric_upsert_entity`, `humetric_get_entity`,
`humetric_list_entities`, `humetric_get_entity_metrics`,
`humetric_explain_metric`, `humetric_metric_history`

**Sorgulama** — `humetric_query_entities`

**Metric Pack** — `humetric_list_packs`, `humetric_get_pack`,
`humetric_create_pack`, `humetric_update_pack`

**İnsan onayı** — `humetric_list_pending_review`, `humetric_review_metric`

**Rıza (KVKK)** — `humetric_get_consent`, `humetric_grant_consent`,
`humetric_revoke_consent`

**Hesap** — `humetric_dashboard`, `humetric_usage_report`,
`humetric_audit_logs`, `humetric_health`

### Kaynaklar

`humetric://packs` · `humetric://dashboard`

### Hazır iş akışları (prompts)

`analyze_entity` · `investigate_signal` · `draft_metric_pack`

## Kapsam dışı bırakılanlar

API'de 41 uç var, 24'ü tool. Aşağıdakiler **bilerek** dışarıda:

| Uç | Neden |
|---|---|
| `/register`, `/login`, `/verify-email` | Hesap açılışı; MCP oturumu zaten kimlik doğrulanmış |
| `/api-keys` (POST/DELETE), `/tenant/rotate-api-key` | Agent kendi erişim anahtarlarını yönetmemeli |
| `/tenant/keys` | BYOK sağlayıcı sırları |
| `/billing/*` | Para hareketi ve Stripe callback'i |
| `/admin/usage` | Tenant sınırları ötesinde admin raporu |
| `/packs/wizard` | YAML'ı LLM'e yazdırır; buradaki LLM zaten Claude — kendisi yazıp `humetric_create_pack` ile yayınlar |

## Tasarım notları

**Sinyaller asenkron işlenir.** `humetric_ingest_signal` genelde `status:
"received"` döner; metrikler hemen hazır olmaz. Sunucunun `instructions`
bloğu Claude'a önce `humetric_get_signal` ile durumu izlemesini, `completed`
olmadan metrik okumamasını söyler.

**Yanıtlar kırpılır.** Uzun listeler `HUMETRIC_MCP_MAX_ITEMS` sınırında kesilir
ve yanıta kaç öğenin gizlendiği yazılır — Claude elindekini tam liste sanıp
yanlış sonuca varmasın diye.

**Hatalar eyleme dönüştürülebilir.** API'nin hata zarfı (`code` + `message` +
`doc_url`) olduğu gibi aktarılır. Örneğin sunucudaki LLM anahtarı geçersizse
Claude `[502 llm_auth_failed] LLM sağlayıcısı API anahtarını reddetti...`
görür; "bir hata oluştu" değil.

**Bağlantı hatasında bir kez yeniden denenir.** API 5xx dönüp bağlantıyı
kapattığında havuzdaki keep-alive girdisi ölü kalır ve sonraki istek
`httpx.ReadError` alır — üstelik bu istisnanın mesajı boştur. Havuz tazelenip
istek bir kez tekrarlanır.

**Eksik anahtar sunucuyu düşürmez.** `HUMETRIC_MCP_API_KEY` yoksa sunucu yine
açılır ve ilk tool çağrısında ne yapılması gerektiğini düz Türkçe söyler.
Başlangıçta `sys.exit(1)` çağırmak, Claude Desktop'ta yalnızca "server failed
to start" olarak görünür ve sebebi kullanıcıya hiç ulaşmaz.

**stdout dokunulmazdır.** stdio transport'unda stdout yalnızca protokol
mesajları içindir; tüm loglar stderr'e yazar.

## Sorun giderme

| Belirti | Kontrol |
|---|---|
| "server failed to start" | Yolların mutlak olduğunu ve `.venv/bin/python`'un doğru olduğunu doğrulayın |
| Tüm tool'lar kimlik hatası | `HUMETRIC_MCP_API_KEY` env bloğunda mı, anahtar iptal edilmiş mi |
| `llm_auth_failed` | Sunucudaki `ANTHROPIC_API_KEY` geçersiz/iptal — HuMetric `.env`'ini güncelleyin |
| Sinyaller `queued`de kalıyor | `humetric_health` → worker çalışıyor mu |
