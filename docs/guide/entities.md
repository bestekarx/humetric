# Entities

An entity is any measurable asset in your domain — a customer, employee, dealer, product, or region. Each entity has a profile, computed metrics, and a vector embedding.

## Entity Model

```json
{
  "id": "customer-42",
  "type": "customer",
  "fields": {
    "name": "Acme Corp",
    "industry": "Manufacturing",
    "region": "EMEA"
  },
  "metrics": {
    "growth_trajectory": { "value": 0.72, "confidence": 0.88 },
    "churn_risk": { "value": -0.45, "confidence": 0.65 }
  },
  "embedding": [0.023, -0.451, ...],
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-01-15T12:00:00Z"
}
```

## Creating an Entity

```
POST /v1/entities
```

```json
{
  "id": "customer-42",
  "type": "customer",
  "fields": {
    "name": "Acme Corp",
    "industry": "Manufacturing",
    "region": "EMEA"
  }
}
```

The entity `id` must be unique within the tenant. `type` must match a defined metric pack.

## Retrieving an Entity

```
GET /v1/entities/{id}
```

Returns the full entity profile including current metrics.

## Entity Types and Metric Packs

Entity types are defined by metric packs. A metric pack specifies:
- Required and optional fields for the entity
- Metric dimensions to track
- KVKK sensitivity classifications
- Confidence thresholds for metric application

See the [Metric Packs API reference](/api-reference) for pack management.

## Embedding

Each entity has a vector embedding generated from its fields and metric values. Embeddings power:
- Semantic search via `/v1/query`
- Entity similarity comparisons
- Clustering and segmentation

The embedding is recalculated whenever metrics change (after signal processing) using the tenant's configured embedding provider (default: Voyage AI, overridable via BYO-Key).

## Entity Metrics

Metrics are calibrated scores on the [-1, 1] range with associated confidence values:

```
GET /v1/entities/{id}/metrics
```

```json
{
  "entity_id": "customer-42",
  "metrics": [
    {
      "metric_key": "growth_trajectory",
      "value": 0.72,
      "confidence": 0.88,
      "last_updated": "2026-01-15T12:00:00Z"
    }
  ]
}
```

### Temporal Decay

Metric values decay toward zero over time. The decay rate is configurable per metric in the metric pack definition. A recent strong signal carries more weight than an older one.

## Query

```
POST /v1/query
```

Search and rank entities by semantic similarity:

```json
{
  "text": "high growth enterprise customers",
  "entity_type": "customer",
  "limit": 10,
  "filters": {
    "fields.industry": "Manufacturing"
  }
}
```

Results are ranked by cosine similarity against the query embedding.
