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

Create API keys via `POST /v1/api-keys`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Health check |
| POST | `/v1/entities` | Create or update entity |
| GET | `/v1/entities/{id}` | Get entity with metrics |
| GET | `/v1/entities/{id}/metrics` | Get entity metrics |
| POST | `/v1/signals` | Submit signal for async processing |
| GET | `/v1/signals/{id}` | Get signal status |
| GET | `/v1/signals/{id}/trace` | Get signal trace |
| POST | `/v1/query` | Search and rank entities |
| POST | `/v1/packs` | Create metric pack |
| GET | `/v1/packs` | List metric packs |
| GET | `/v1/packs/{key}` | Get pack detail |
| PUT | `/v1/packs/{key}` | Update pack |
| POST | `/v1/packs/wizard` | AI-generated pack |
| POST | `/v1/api-keys` | Create API key |
| GET | `/v1/api-keys` | List API keys |
| DELETE | `/v1/api-keys/{id}` | Revoke API key |
| POST | `/v1/consent` | Grant consent |
| GET | `/v1/consent/{entity_id}` | List consents |
| DELETE | `/v1/consent/{entity_id}` | Revoke consent |
| GET | `/v1/tenant/keys` | Get BYO-key status |
| PUT | `/v1/tenant/keys` | Update BYO-keys |
| DELETE | `/v1/tenant/keys` | Remove BYO-keys |
| GET | `/v1/usage` | Daily usage totals over a date range |
| GET | `/v1/usage/calls` | Per-request call history (by tool, key, client, …) |
| GET | `/v1/usage/packs` | Usage broken down by Metric Pack |

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
     "entity_count": 14, "llm_token_count": 91240, "model": "deepseek:deepseek-chat"},
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
