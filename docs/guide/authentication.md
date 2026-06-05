# Authentication

All HuMetric API requests are authenticated with API keys passed as Bearer tokens.

## API Keys

API keys are scoped tokens that grant access to specific operations. Each tenant manages its own keys independently.

### Key Scopes

| Scope | Description |
|-------|-------------|
| `signals:write` | Submit signals for processing |
| `signals:read` | Read signal status and traces |
| `entities:read` | Read entity profiles and metrics |
| `entities:write` | Create or update entities |
| `packs:read` | Read metric pack definitions |
| `packs:write` | Create and update metric packs |
| `packs:wizard` | Use AI pack generation wizard |
| `query` | Search and rank entities |
| `api_keys:manage` | Create and revoke API keys |
| `consent:manage` | Manage consent records |
| `tenant:manage` | Update tenant settings and BYO-keys |
| `admin` | Full tenant access (all scopes) |

### Creating an API Key

```bash
curl -X POST https://api.humetric.io/v1/api-keys \
  -H "Authorization: Bearer YOUR_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prefix": "myapp", "scopes": ["signals:write", "entities:read", "query"]}'
```

The response includes the `full_key` — save it immediately. Only the key prefix is stored; the full key cannot be retrieved later.

```json
{
  "id": "key_abc123",
  "prefix": "myapp",
  "full_key": "hum_myapp_x9K2mP7vL4nQ8wR3tY6u",
  "scopes": ["signals:write", "entities:read", "query"],
  "created_at": "2026-01-15T10:30:00Z"
}
```

### Listing Keys

```bash
curl https://api.humetric.io/v1/api-keys \
  -H "Authorization: Bearer YOUR_ADMIN_KEY"
```

### Revoking a Key

```bash
curl -X DELETE https://api.humetric.io/v1/api-keys/key_abc123 \
  -H "Authorization: Bearer YOUR_ADMIN_KEY"
```

## Using API Keys

Include the key in the `Authorization` header of every request:

```
Authorization: Bearer hum_myapp_x9K2mP7vL4nQ8wR3tY6u
```

SDKs handle this automatically when instantiated with an API key:

```python
client = HumetricClient(api_key="hum_myapp_x9K2mP7vL4nQ8wR3tY6u")
```

## Rate Limiting

API keys are subject to rate limits configured per tenant. Responses include `X-RateLimit-*` headers. Exceeding the limit returns HTTP 429.

## Security Best Practices

- Rotate keys regularly via create-and-revoke
- Use the most restrictive scopes needed (principle of least privilege)
- Never embed API keys in client-side code or version control
- Use `prefix` for audit-friendly key identification
