# Authentication

All HuMetric API requests are authenticated with API keys passed as Bearer tokens.

## API Keys

API keys are scoped tokens that grant access to specific operations. Each tenant manages its own keys independently.

### Key Scopes

There are exactly eight scopes:

| Scope | Description |
|-------|-------------|
| `signals:write` | Submit signals for processing |
| `signals:read` | Read signal status and traces |
| `entities:read` | Read entity profiles, metrics, history, and audit logs |
| `entities:write` | Create or update entities, and manage consent records |
| `query` | Search and rank entities |
| `packs:read` | Read metric pack definitions |
| `packs:admin` | Create and update packs, use the pack wizard, resolve pending metric reviews |
| `tenant:admin` | Tenant settings, BYO-keys, usage reports, billing, exports |

There is no `admin`, `packs:write`, `api_keys:manage`, `consent:manage`, or `tenant:manage` scope — consent is covered by `entities:*`, and pack administration by `packs:admin`.

Key management itself (`POST`/`GET`/`DELETE /v1/api-keys`) requires **no specific scope**, only a valid credential. What it does enforce is that a new key cannot exceed the creator's own scopes; asking for more returns `403 insufficient_scopes`.

### Key prefixes

`prefix` is not a free-form label. It must be either `hm_live` or `hm_test` (default `hm_test`), and it becomes the leading segment of the key itself. Use `label` for human-readable identification.

### Creating an API Key

```bash
curl -X POST https://api.gethumetric.com/v1/api-keys \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "prefix": "hm_live",
        "label": "myapp-production",
        "scopes": ["signals:write", "entities:read", "query"],
        "expires_in_days": 90
      }'
```

Omitting `scopes` inherits the creator's scopes. `expires_in_days` accepts 1–730; alternatively pass an explicit `expires_at`.

The response (**201**) includes `full_key` — save it immediately. Only a SHA-256 hash is stored; the full key cannot be retrieved later.

```json
{
  "id": 42,
  "prefix": "hm_live",
  "full_key": "hm_live_<32-byte-url-safe-random>",
  "scopes": ["signals:write", "entities:read", "query"],
  "label": "myapp-production",
  "message": "Store this key securely. It will not be shown again."
}
```

`id` is an integer.

### Listing Keys

```bash
curl https://api.gethumetric.com/v1/api-keys \
  -H "Authorization: Bearer YOUR_KEY"
```

Each row reports `id`, `prefix`, `scopes`, `label`, `is_revoked`, `last_used_at`, `created_at`, and `expires_at` — never the key itself.

### Revoking a Key

```bash
curl -X DELETE https://api.gethumetric.com/v1/api-keys/42 \
  -H "Authorization: Bearer YOUR_KEY"
```

The path takes the integer `id` from the list response.

## Using API Keys

Include the key in the `Authorization` header of every request:

```
Authorization: Bearer hm_live_...
```

SDKs handle this automatically when instantiated with an API key:

```python
client = HumetricClient(api_key="hm_live_...")
```

### Dashboard sessions

`POST /v1/login` (email + password) returns a signed session token that can be used as a Bearer credential instead of an API key. It is valid for 24 hours and carries all eight scopes. It is intended for the dashboard, not for integrations — an expired token returns `401 session_expired`, which is a distinct code from `401 invalid_api_key` so a client can tell "sign in again" apart from "your key is wrong".

### Public endpoints

These require no credential: `/healthz`, `/healthz/db`, `/healthz/worker`, `/metrics`, `/v1/register`, `/v1/login`, `/v1/verify-email`, `/v1/billing/webhook` (authenticated by the provider's signature instead), and the OpenAPI/docs routes.

## Rate Limiting

API keys are subject to a per-tenant, per-minute rate limit. Responses include `X-RateLimit-*` headers, and exceeding the limit returns HTTP 429 with `Retry-After`. Health checks are not counted.

Two unauthenticated endpoints carry their own limit, keyed by client IP instead of tenant because no tenant exists yet at that point: `POST /v1/register` and `POST /v1/login`, both per hour. The login limit is a brute-force guard — there is no account lockout, so a wrong password never blocks the account itself. Every other unauthenticated request is uncounted.

Separately, tier limits are enforced on writes: exceeding your plan's signal, entity, or pack allowance returns HTTP 402 `tier_limit_exceeded` with an upgrade URL.

## Security Best Practices

- Rotate keys regularly via create-and-revoke, or use `POST /v1/tenant/rotate-api-key`
- Use the most restrictive scopes needed (principle of least privilege) — a key can never grant more than its creator held
- Set `expires_at` / `expires_in_days` on integration keys
- Never embed API keys in client-side code or version control
- Use `hm_test` for non-production traffic so usage reports stay separable
- Key creation, rejection, and revocation are all written to the tenant audit log (`GET /v1/audit-logs`)
