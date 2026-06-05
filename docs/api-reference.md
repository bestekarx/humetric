# API Reference

The complete API reference is available as an OpenAPI 3.1 specification.

## Interactive Docs

Explore the API interactively at [api.humetric.io/docs](https://api.humetric.io/docs).

## OpenAPI Spec

Raw OpenAPI JSON: [api.humetric.io/openapi.json](https://api.humetric.io/openapi.json)

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

## Error Format

All errors follow this format:

```json
{
  "error": {
    "code": "entity_not_found",
    "message": "Entity with ID 'xyz' not found",
    "doc_url": "https://docs.humetric.dev/errors/entity_not_found"
  }
}
```
