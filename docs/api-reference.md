# API Reference

The complete API reference is available as an OpenAPI 3.1 specification.

## Interactive Docs

Explore the API interactively at [api.gethumetric.com/docs](https://api.gethumetric.com/docs).

## OpenAPI Spec

Raw OpenAPI JSON: [api.gethumetric.com/openapi.json](https://api.gethumetric.com/openapi.json)

## Authentication

All API requests require a Bearer token (API key) in the Authorization header:

```
Authorization: Bearer YOUR_API_KEY
```

Create API keys via `POST /v1/api-keys`. The scope each route needs is listed below; see the [Authentication guide](/guide/authentication) for the full scope list.

## Endpoints

### Health (public)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Liveness check |
| GET | `/healthz/db` | Database connectivity |
| GET | `/healthz/worker` | Queue depth, failures/hour, oldest queued task |

### Entities and metrics

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| POST | `/v1/entities` | `entities:write` | Create (201) or update (200) an entity |
| GET | `/v1/entities` | `entities:read` | List entities |
| GET | `/v1/entities/{id}` | `entities:read` | Get entity with metrics |
| GET | `/v1/entities/{id}/metrics` | `entities:read` | Current metrics (`?include_history=true` for sparklines) |
| GET | `/v1/entities/{id}/metrics/{key}/explain` | `entities:read` | Which signals moved this metric |
| GET | `/v1/entities/{id}/metrics/{key}/history` | `entities:read` | Full metric time series |
| PUT | `/v1/metrics/{entity_id}/{key}/review` | `packs:admin` | Resolve a flagged metric with a reviewer override |
| GET | `/v1/metrics/pending-review` | `packs:admin` | Metrics awaiting human review |

### Signals

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| POST | `/v1/signals` | `signals:write` | Submit signal for async processing (202) |
| GET | `/v1/signals/{id}` | `signals:read` | Get signal status |
| GET | `/v1/signals/{id}/trace` | `signals:read` | Get signal trace and evidence |
| GET | `/v1/entities/{id}/signals` | `signals:read` | List an entity's signals |

### Query

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| POST | `/v1/query` | `query` | Search and rank entities |

### Metric packs

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| POST | `/v1/packs` | `packs:admin` | Create metric pack |
| GET | `/v1/packs` | `packs:read` | List metric packs (`?is_active=`) |
| GET | `/v1/packs/{key}` | `packs:read` | Get pack detail |
| PUT | `/v1/packs/{key}` | `packs:admin` | Update pack (increments `version`) |
| POST | `/v1/packs/wizard` | `packs:admin` | Generate pack YAML — returns it, does not save |

### API keys and audit

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| POST | `/v1/api-keys` | auth only | Create API key (cannot exceed creator's scopes) |
| GET | `/v1/api-keys` | auth only | List API keys |
| DELETE | `/v1/api-keys/{id}` | auth only | Revoke API key |
| GET | `/v1/audit-logs` | `entities:read` | Tenant audit trail |

### Consent (KVKK / GDPR)

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| POST | `/v1/consent` | `entities:write` | Grant consent for a scope |
| GET | `/v1/consent/{entity_id}` | `entities:read` | List consents |
| DELETE | `/v1/consent/{entity_id}` | `entities:write` | Revoke consent |

### Tenant, usage, billing

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| GET | `/v1/tenant/keys` | `tenant:admin` | BYO-key presence flags |
| PUT | `/v1/tenant/keys` | `tenant:admin` | Set BYO-keys / active provider |
| DELETE | `/v1/tenant/keys` | `tenant:admin` | Remove all BYO-keys |
| GET | `/v1/tenant/dashboard` | `tenant:admin` | Tier, quota consumption, trial state |
| POST | `/v1/tenant/export` | `tenant:admin` | Request a raw-data export |
| POST | `/v1/tenant/start-trial` | `tenant:admin` | Start the trial period |
| POST | `/v1/tenant/rotate-api-key` | `tenant:admin` | Rotate the primary key |
| GET | `/v1/usage` | `tenant:admin` | Daily usage totals over a date range |
| GET | `/v1/usage/calls` | `tenant:admin` | Per-request call history (by tool, key, client, …) |
| GET | `/v1/usage/packs` | `tenant:admin` | Usage broken down by Metric Pack |
| GET | `/v1/admin/usage` | `packs:admin` | Cross-tenant usage (operator view) |
| POST | `/v1/billing/checkout` | `tenant:admin` | Start a Stripe checkout session |

### Registration and login (public)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/register` | Register a tenant (captcha-protected) |
| GET | `/v1/verify-email` | Confirm the emailed verification token |
| POST | `/v1/login` | Email + password → 24-hour session token |
| POST | `/v1/billing/webhook` | Stripe webhook (authenticated by provider signature) |

Prometheus metrics are exposed at `/metrics`.

### Usage reporting

`/v1/usage` answers "what did we spend per day"; `/v1/usage/packs` answers
"which pack cost how much". Both require the `tenant:admin` scope and take
`start_date` / `end_date` (`YYYY-MM-DD`, both inclusive).

Some LLM spend belongs to no pack — query re-ranking and pack-wizard
generations. Those appear in `/v1/usage/packs` as rows with `kind: "system"`
and a human-readable `label`, carrying token counts but no entity/signal
counts. They are included so the pack breakdown reconciles with the daily
total: summing `llm_token_count` across all rows for a window equals the
`/v1/usage` total for that window.

```json
{
  "packs": [
    {"pack_key": "tcmb-ppk", "kind": "pack", "signal_count": 28,
     "entity_count": 14, "llm_token_count": 91240, "model": "<provider>:<model>"},
    {"pack_key": "__wizard__", "kind": "system", "label": "Pack wizard",
     "signal_count": 0, "entity_count": 0, "llm_token_count": 6110}
  ]
}
```

Pack keys beginning with `__` are reserved for these system rows and are
rejected by `POST /v1/packs`.

## Error Format

All errors follow this format:

```json
{
  "error": {
    "code": "entity_not_found",
    "message": "Entity with ID 'xyz' not found",
    "doc_url": "https://gethumetric.com/docs/errors/entity_not_found"
  }
}
```
