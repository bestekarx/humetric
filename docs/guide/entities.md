# Entities

An entity is any measurable asset in your domain — a customer, employee, dealer, product, or region. Each entity has a profile, computed metrics, and a vector embedding.

## Entity Model

```json
{
  "id": "customer-42",
  "entity_type": "customer",
  "fields": {
    "name": "Acme Corp",
    "industry": "Manufacturing",
    "region": "EMEA"
  },
  "free_text": "Long-standing account, renewed twice.",
  "metrics": [
    {
      "metric_key": "growth_trajectory",
      "value": 0.72,
      "confidence": 0.88,
      "effective_confidence": 0.85,
      "source_count": 4,
      "last_updated": "2026-01-15T12:00:00Z"
    }
  ],
  "status": "active",
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-01-15T12:00:00Z"
}
```

The embedding vector is stored but never returned by the API.

## Creating an Entity

```
POST /v1/entities
```

```json
{
  "id": "customer-42",
  "entity_type": "customer",
  "fields": {
    "name": "Acme Corp",
    "industry": "Manufacturing",
    "region": "EMEA"
  },
  "free_text": "Optional background context used for embeddings."
}
```

The field is `entity_type` (`entityType` is also accepted) — not `type`. The entity `id` must be unique within the tenant and match `^[a-zA-Z0-9\-_.]+$`.

`entity_type` must match a metric pack that is active for the tenant. The pack's `required_fields` are enforced here: a missing field returns `422 missing_required_fields`, an unrecognised type returns `422 unknown_entity_type` or `422 no_active_pack_for_type`.

The route is an upsert: a new entity returns **201**, an existing one is updated and returns **200**.

## Retrieving an Entity

```
GET /v1/entities/{id}
```

Returns the full entity profile including current metrics. `GET /v1/entities` lists them with pagination.

## Entity Types and Metric Packs

Entity types are defined by metric packs. A metric pack specifies:
- Required and optional fields for the entity
- Metric dimensions to track
- KVKK sensitivity classifications and the consent scope each sensitive metric needs
- Per-metric type, direction, unit, and display bands

Only **one** pack can be active per entity type. If a pack exists for the type but is inactive, writes return `403 entity_type_locked`.

See the [Metric Packs API reference](/api-reference) for pack management.

## Embedding

Each entity has a single vector embedding generated from its `free_text`, its scalar `fields`, and its **non-sensitive** metric values. Metrics listed in the pack's `kvkk.sensitive_metrics` are excluded, so sensitive data never leaks into the vector. Embeddings power:
- Semantic search via `/v1/query`
- Entity similarity comparisons
- Clustering and segmentation

The embedding is refreshed after each completed signal using the tenant's configured embedding provider (default: Voyage AI, overridable via BYO-Key). If embedding fails, the entity is flagged and a re-embed job is queued — the metric write itself is not lost.

## Entity Metrics

Metrics are calibrated scores on the [-1, 1] range with associated confidence values:

```
GET /v1/entities/{id}/metrics
```

```json
{
  "entity_id": "customer-42",
  "metric_count": 5,
  "metrics": [
    {
      "metric_key": "growth_trajectory",
      "value": 0.72,
      "confidence": 0.88,
      "effective_confidence": 0.8748,
      "source_count": 4,
      "last_updated": "2026-01-15T12:00:00Z",
      "source_signal_id": "sig_01H...",
      "context_hash": "9f2c..."
    }
  ],
  "history": {}
}
```

### Inline history

`history` is present but **empty** unless you pass `?include_history=true`. With it, `history` becomes an object **keyed by metric key** — the points are not nested inside each metric object:

```json
{
  "history": {
    "growth_trajectory": [
      {
        "recorded_at": "2026-01-15T12:00:00Z",
        "value": 0.72,
        "prev_value": 0.45,
        "delta": 0.27,
        "confidence": 0.88,
        "source_count": 4,
        "signal_id": "sig_01H...",
        "model": "<provider-model>",
        "reasoning": "Upgrade to premium plan indicates consistent growth",
        "source_span": "upgraded to premium plan"
      }
    ]
  }
}
```

Up to 30 points per metric — enough for a sparkline. Full history and evidence live at `/v1/entities/{id}/metrics/{key}/history` and `/explain`.

### Temporal Decay

Decay applies to **confidence, not value**, and it is applied at read time — the stored `confidence` is never modified:

```
effective_confidence = confidence × e^(−λ · age_in_days)
```

`λ` defaults to `ln2 / 365` (a one-year half-life) and is a deployment-level setting, not a per-metric pack field. Because the multiplication happens on read, the same data always yields the same stored record and history stays recomputable. Compare `confidence` with `effective_confidence` to see how stale a score is.

## Query

```
POST /v1/query
```

Search and rank entities by hybrid similarity:

```json
{
  "free_text_query": "high growth enterprise customers",
  "entity_type": "customer",
  "top_k": 10,
  "filters": {
    "industry": "Manufacturing"
  },
  "rank_by": "growth_trajectory",
  "include_reasoning": false
}
```

The field names are `free_text_query` and `top_k` (max 100). `filters` matches against the entity's `fields` JSON.

Ranking combines pgvector cosine similarity with a PostgreSQL full-text rank over `free_text`. Setting `include_reasoning: true` adds an LLM re-ranking pass that returns a short justification per result; that pass consumes tokens and is billed to the `__ranker__` sentinel pack in usage reports.
