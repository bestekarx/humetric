# Signals

A signal is a free-text observation about an entity, optionally accompanied by structured key-value data. Signals are the primary input to HuMetric's metric extraction pipeline.

## Signal Flow

```
Client submits signal
       │
       ▼
  202 Accepted  →  status: "received"
       │
       ▼
  Task Queue (async, SELECT FOR UPDATE SKIP LOCKED)
       │
       ├── LLM extraction — the tenant's configured provider infers metrics
       ├── Deterministic merge — confidence-weighted average, no model call
       ├── Consent gate — sensitive metrics need a granted consent scope
       ├── Evidence check — the cited source_span must appear verbatim
       ├── Metric write — current value + append-only history row
       └── Re-embedding — entity vector refreshed
       │
       ▼
  Signal status: "completed"  (or "failed")
```

Only extraction calls an LLM. The merge step is plain Python, so a signal costs exactly one model call.

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
  "structured": {
    "source": "salesforce",
    "record_id": "opp-9876"
  },
  "external_id": "opp-9876",
  "occurred_at": "2026-01-15T09:00:00Z"
}
```

| Field | Notes |
|-------|-------|
| `entity_id`, `entity_type` | Required. The entity must already exist and be `active`. |
| `text` | Up to 300 000 characters. The whole string is forwarded into the extraction prompt, so length drives token cost. |
| `structured` | Arbitrary JSON. If `text` is omitted, this is serialised and extracted from instead. |
| `external_id` | Your own identifier for the source record; used for duplicate detection. **Ignored if an `Idempotency-Key` header is present** — see below. |
| `occurred_at` | When the source text was produced — the timeline axis of metric history. Omit for live signals; supply it when backfilling. Cannot be in the future. |

### Response (202 Accepted)

```json
{
  "signal_id": "sig_x7K3pM",
  "status": "received",
  "trace_url": "/v1/signals/sig_x7K3pM/trace"
}
```

The signal is accepted immediately and processed asynchronously. Use the `signal_id` to poll for completion.

### Error responses

| Status | Code | Cause |
|--------|------|-------|
| 404 | `entity_not_found` | No such entity for this tenant |
| 403 | `entity_archived` | The entity is archived |
| 403 | `entity_type_locked` | A pack exists for the type but is inactive |
| 409 | `duplicate_external_id` | `external_id` already used for this entity (see below) |
| 402 | `tier_limit_exceeded` | Monthly signal quota reached |

## Idempotency and duplicates

Both mechanisms write the same `signal.external_id` column, and **the header wins**:

```
stored external_id = Idempotency-Key header  OR  body.external_id
```

This precedence is easy to miss: **if you send the header, the `external_id` in your body is silently discarded** and never stored. Send one or the other, not both.

**`Idempotency-Key` header — safe retry.** A repeat within 24 hours replays the original result with **HTTP 200**, including the extracted metrics if processing has finished, and creates no new signal or work.

```bash
curl -X POST https://api.gethumetric.com/v1/signals \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: sig-001" \
  -d '{"entity_id": "customer-42", "entity_type": "customer", "text": "..."}'
```

**`external_id` in the body — duplicate rejection.** With no header present, re-submitting an `external_id` already recorded for that entity returns **409 `duplicate_external_id`**, not a replay. Two concurrent requests racing on the same value also resolve to 409 rather than a 500.

Choose by intent: the header if a retry should be harmless, `external_id` if a second submission of the same source record should be refused. Sending both does **not** give you both behaviours — you get the header's, and the body value is lost.

## Checking Signal Status

```
GET /v1/signals/{signal_id}
```

```json
{
  "signal_id": "sig_x7K3pM",
  "status": "completed",
  "entity_id": "customer-42",
  "error": null,
  "occurred_at": "2026-01-15T09:00:00Z",
  "created_at": "2026-01-15T10:30:00Z",
  "processed_at": "2026-01-15T10:30:12Z"
}
```

### Signal States

| Status | Description |
|--------|-------------|
| `received` | Accepted, queued, awaiting the worker |
| `completed` | Metrics extracted and applied — **this is the terminal success value** |
| `failed` | Processing error, details in `error` |

`processing` exists in the schema but the worker does not use it: a signal goes from `received` straight to `completed` or `failed`. Poll for `completed`, not `complete`.

Retries are transparent. A retryable failure returns the signal to `received` with exponential backoff (up to 3 attempts) before it lands in `failed`; a 4xx from the provider is treated as permanent and fails immediately.

## Signal Trace

Each signal carries a trace explaining which metrics changed and on what evidence:

```
GET /v1/signals/{signal_id}/trace
```

```json
{
  "signal_id": "sig_x7K3pM",
  "entity_id": "customer-42",
  "trace_data": {
    "metrics": [
      {
        "metric_key": "growth_trajectory",
        "value": 0.72,
        "confidence": 0.88,
        "source_count": 4,
        "source_signal_id": "sig_x7K3pM",
        "needs_review": false,
        "reasoning": "Upgrade to premium plan indicates consistent growth",
        "source_span": "upgraded to premium plan after 6 months of growth"
      }
    ],
    "text": "Customer upgraded to premium plan after 6 months of growth",
    "structured": {},
    "status": "completed",
    "occurred_at": "2026-01-15T09:00:00Z",
    "created_at": "2026-01-15T10:30:00Z",
    "entity_metrics": []
  }
}
```

`source_span` is the model's verbatim quote from the signal text — the console highlights it inside the raw text. The worker checks that the quote actually appears in the text; if it does not, or if the quote itself reads like an instruction to the model, the metric is still written but flagged for human review and appears in `GET /v1/metrics/pending-review`.

## Structured data

The `structured` field accepts arbitrary key-value pairs (stored as JSONB). Use it to carry source-system references alongside the observation. When `text` is absent, `structured` becomes the extraction input, so keep it meaningful rather than purely technical.
