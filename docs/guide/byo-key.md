# BYO-Key (Bring Your Own Key)

BYO-Key lets tenants use their own LLM and embedding provider API keys instead of relying on HuMetric's default keys. This gives you full control over costs, vendor relationships, and data residency.

## Supported Providers

| Provider | Service | Key Type |
|----------|---------|----------|
| Anthropic | Claude (LLM extraction and curation) | `anthropic_api_key` |
| Voyage AI | Embedding generation | `voyage_api_key` |

## Checking Key Status

```
GET /v1/tenant/keys
```

```json
{
  "anthropic_api_key": {
    "set": true,
    "masked": "sk-...abc123"
  },
  "voyage_api_key": {
    "set": false,
    "masked": null
  }
}
```

## Setting BYO-Keys

```
PUT /v1/tenant/keys
```

```json
{
  "anthropic_api_key": "sk-ant-api03-your-key-here",
  "voyage_api_key": "vp-your-key-here"
}
```

Keys are encrypted at rest with AES-256. Only masked versions are visible after setting.

## Removing BYO-Keys

```
DELETE /v1/tenant/keys
```

```json
{
  "remove": ["anthropic_api_key"]
}
```

Removing a key causes HuMetric to fall back to its default provider key for that service.

## Fallback Behavior

If a BYO-key is set but the provider returns an error (e.g., quota exceeded, invalid key):

1. The request fails with a clear error message indicating the provider issue.
2. HuMetric does **not** automatically fall back to default keys — this prevents unexpected charges on the platform's keys.
3. Once you fix or remove the problematic key, processing resumes normally.

## Cost Implications

- **With BYO-Keys**: You pay your provider directly. HuMetric does not charge for AI processing.
- **Without BYO-Keys**: AI processing costs are included in your HuMetric plan.
- **Mixed**: You can BYO only Anthropic and use HuMetric's Voyage key, or vice versa.

## Security

- Keys are encrypted at rest using tenant-specific AES-256 encryption
- Keys are never logged or exposed in API responses beyond the masked prefix
- Key access is scoped to the `tenant:manage` permission
- All key operations are audited to the tenant audit log
