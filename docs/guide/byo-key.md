# BYO-Key (Bring Your Own Key)

BYO-Key lets tenants use their own LLM and embedding provider API keys instead of relying on HuMetric's default keys. This gives you full control over costs, vendor relationships, and data residency.

All key endpoints require the `tenant:admin` scope.

## Supported Providers

HuMetric is multi-provider. One LLM provider is active at a time, selected with `llm_provider`; the embedding provider is configured separately.

| Purpose | Provider | Request field |
|---------|----------|---------------|
| LLM extraction | Anthropic | `anthropic_key` |
| LLM extraction | OpenAI | `openai_key` |
| LLM extraction | Google AI | `google_ai_key` |
| LLM extraction | DeepSeek | `deepseek_key` |
| Embeddings | Voyage AI | `voyage_key` |

Only **extraction** calls an LLM. Curation is a deterministic Python merge with no model call, so a BYO LLM key is consumed once per signal, not twice.

## Checking Key Status

```
GET /v1/tenant/keys
```

The response reports **presence only** — a stored key is never returned, not even masked.

```json
{
  "has_anthropic_key": true,
  "has_voyage_key": true,
  "has_openai_key": false,
  "has_google_ai_key": false,
  "has_deepseek_key": false,
  "llm_provider": "anthropic",
  "updated_at": "2026-09-01T10:12:00Z"
}
```

## Setting BYO-Keys

```
PUT /v1/tenant/keys
```

Send only the fields you want to change; omitted fields are left untouched.

```json
{
  "anthropic_key": "<your-anthropic-key>",
  "voyage_key": "<your-voyage-key>",
  "llm_provider": "anthropic"
}
```

To switch provider, send that provider's key and set `llm_provider` in the same call:

```json
{
  "openai_key": "<your-openai-key>",
  "llm_provider": "openai"
}
```

`llm_provider` must be one of the providers enabled on the deployment; anything else fails validation. The response is the same `has_*_key` shape as `GET`.

Keys are encrypted at rest with AES-256. If the deployment has no encryption key configured, all three endpoints return `501 byo_key_unavailable` rather than storing anything in plaintext.

## Removing BYO-Keys

```
DELETE /v1/tenant/keys
```

Takes **no request body** and removes **all** stored keys at once — there is no per-key removal. `llm_provider` is reset to `anthropic` so a tenant that clears its keys falls back to the platform key instead of being left with a keyless provider selected.

## Fallback Behavior

If a BYO-key is set but the provider returns an error (e.g., quota exceeded, invalid key):

1. The request fails with a provider-specific error code — `llm_auth_failed`, `llm_quota_exhausted`, `llm_rate_limited`, or `llm_unavailable`.
2. HuMetric does **not** automatically fall back to default keys — this prevents unexpected charges on the platform's keys.
3. Once you fix the key or clear it with `DELETE`, processing resumes normally.

Failed signals are retried with exponential backoff, so fixing the key recovers the queued work without re-ingesting.

## Cost Implications

- **With BYO-Keys**: You pay your provider directly. HuMetric does not charge for AI processing.
- **Without BYO-Keys**: AI processing costs are included in your HuMetric plan.
- **Mixed**: You can BYO only the LLM key and use HuMetric's Voyage key, or vice versa.

## Security

- Keys are encrypted at rest with AES-256
- Keys are never logged and never returned by the API — reads report presence only
- Key access is scoped to `tenant:admin`
- All key operations are audited to the tenant audit log
