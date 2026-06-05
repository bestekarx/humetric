# Signals

A signal is a free-text observation about an entity, optionally accompanied by structured key-value data. Signals are the primary input to HuMetric's metric extraction pipeline.

## Signal Flow

```
Client submits signal
       │
       ▼
  202 Accepted (immediate)
       │
       ▼
  Task Queue (async)
       │
       ├── LLM Extraction (Claude infers metrics)
       ├── Curator Validation (confidence check)
       ├── Metric Update (temporal decay applied)
       └── Re-embedding (entity vector updated)
       │
       ▼
  Signal status: complete
```

## Submitting a Signal

### Endpoint

```
POST /v1/signals
```

### Request Body

```json
{
  "entity_id": "customer-42",
  "entity_type": "customer",
  "text": "Customer upgraded to premium plan after 6 months of growth",
  "metadata": {
    "source": "salesforce",
    "record_id": "opp-9876"
  }
}
```

### Response (202 Accepted)

```json
{
  "signal_id": "sig_x7K3pM",
  "status": "queued",
  "created_at": "2026-01-15T10:30:00Z"
}
```

The signal is accepted immediately and processed asynchronously. Use the `signal_id` to poll for completion.

## Idempotency

Use the `Idempotency-Key` header to safely retry submissions without creating duplicate signals:

```bash
curl -X POST https://api.humetric.io/v1/signals \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: sig-001" \
  -d '{"entity_id": "customer-42", "entity_type": "customer", "text": "..."}'
```

The same idempotency key returns the original `signal_id` rather than creating a new signal.

## Checking Signal Status

```
GET /v1/signals/{signal_id}
```

```json
{
  "signal_id": "sig_x7K3pM",
  "status": "complete",
  "entity_id": "customer-42",
  "created_at": "2026-01-15T10:30:00Z",
  "completed_at": "2026-01-15T10:30:12Z",
  "metrics_extracted": 3
}
```

### Signal States

| Status | Description |
|--------|-------------|
| `queued` | Awaiting processing |
| `processing` | LLM extraction in progress |
| `complete` | Metrics extracted and applied |
| `failed` | Processing error (details in `error` field) |

## Signal Trace

Each signal produces a decision trace explaining which metrics changed and why:

```
GET /v1/signals/{signal_id}/trace
```

```json
{
  "signal_id": "sig_x7K3pM",
  "decisions": [
    {
      "metric_key": "growth_trajectory",
      "previous_value": 0.45,
      "new_value": 0.72,
      "confidence": 0.88,
      "rationale": "Upgrade to premium plan indicates consistent growth over 6 months"
    }
  ]
}
```

## Metadata

The `metadata` field accepts arbitrary key-value pairs (stored as JSONB). Useful for linking signals back to source systems without polluting the metric extraction logic.
