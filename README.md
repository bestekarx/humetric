<div align="center">

<a href="https://gethumetric.com/">
  <img src="https://gethumetric.com/favicon.svg" alt="HuMetric logo" width="96" height="96" />
</a>

# HuMetric

**Domain-agnostic entity intelligence platform.** Turn unstructured signals into calibrated, decaying entity metrics using LLM-powered agents — no code changes required per domain.

[![CI](https://github.com/bestekarx/humetric/actions/workflows/ci.yml/badge.svg)](https://github.com/bestekarx/humetric/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

[**Website**](https://gethumetric.com/) · [**Dashboard**](https://gethumetric.com/dashboard) · [**Docs**](docs/) · [**Contributing**](CONTRIBUTING.md)

</div>

---

## What It Does

You have entities (workers, dealers, customers, regions, vehicles...) and you receive signals about them — free text observations, structured reports, chat transcripts. HuMetric extracts measurable metrics from every signal, validates them against historical data, and builds a decaying, confidence-weighted profile for each entity.

```
Raw Signal → Extractor Agent (Haiku) → Curator Agent (Sonnet) → Stored Metrics → Hybrid Search + Ranking
     │                │                        │
     │         Extract metrics           Validate & merge
     │         (value, confidence)       (context-aware)
     │
  "Ahmet was 45 minutes late today,
   but his technical work was excellent."
```

**Output:** Every entity gets a numeric profile with confidence scores, updated in real time. Query entities by metric ("find the most punctual workers in Istanbul"), by free text ("which technicians handle angry customers well?"), or by both.

## How It Works

### Core Pipeline

1. **Define your domain** with a Metric Pack (YAML file). Specify entity type, which metrics to track, prompts for extraction/curation, and sensitivity rules.

2. **Register entities** via API. Each entity gets a unique ID, type, optional fields, and free-text description.

3. **Send signals** — any observation about an entity. Signals enter an async task queue.

4. **Background processing:**
   - **Extractor Agent** (Claude Haiku) reads the signal text and extracts structured metrics (key, value, confidence, reasoning)
   - **Curator Agent** (Claude Sonnet) compares against existing metrics, decides to accept/merge/reject, and computes a weighted final value
   - **Temporal decay** is applied — older signals lose weight over time
   - The entity is re-embedded for vector search

5. **Query entities** using hybrid search (vector similarity + full-text) combined with LLM re-ranking.

### Agent Architecture

Each agent handles a single, testable unit of work:

| Agent | Model | Responsibility |
|-------|-------|----------------|
| **Extractor** | Claude Haiku | Parse free text → structured metric tuples (key, value, confidence) |
| **Curator** | Claude Sonnet | Validate extractions against historical data, merge with existing profile |
| **Ranker** | Claude Sonnet | Re-rank hybrid search results based on query intent |
| **Wizard** | Claude Haiku | Generate Metric Pack YAML from natural language description |

All prompts are externalized as Markdown files in `prompts/` and can be overridden per pack.

### Metric Pack System

Metric Packs define **everything** about a domain declaratively — no Python code needed:

```yaml
entity_type: worker
label: "Field Service Worker"
version: 1
required_fields:
  - key: region
    type: str
    label: "Service Region"

metrics:
  - key: punctuality
    label: "Punctuality"
    type: float
    prompt: "On-time arrival, meeting deadlines, delay patterns"
    default_confidence: 0.5

  - key: technical_skill
    label: "Technical Skill"
    type: float
    prompt: "Domain expertise, problem-solving, tool usage"

  - key: financial_status
    label: "Financial Status"
    type: float
    sensitive: true
    visible_to: ["admin"]
    requires_consent_scope: "sensitive_data"

prompts:
  extraction: |
    You are a field service worker performance analyst.
    Extract metrics from the signal: punctuality, work quality, technical skill, communication.

kvkk:
  sensitive_metrics: ["financial_status"]
```

Upload via `POST /v1/packs` — no restart needed.

### Multi-Tenant Isolation

Row-Level Security (RLS) at the PostgreSQL level. Every API key resolves to a tenant; `set_config('app.tenant_id', ...)` enforces isolation. **Fail-closed:** if the tenant context is not set, zero rows are returned.

### KVKK / GDPR Compliance

Three-layer data protection:
1. **Database layer:** Consent records with grant/revoke/expire lifecycle
2. **Schema layer:** Per-metric `sensitive` flag + `visible_to` scopes + `requires_consent_scope`
3. **Embedding layer:** Sensitive metrics are excluded from embedding vectors

Consent is checked per-metric, per-entity at query time. Revoking consent immediately hides the metric from all read paths.

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+
- Anthropic API key
- Voyage AI API key

### Setup

```bash
# Clone and configure
git clone https://github.com/bestekarx/humetric.git
cd humetric
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY, VOYAGE_API_KEY, and set HUMETRIC_AUTH_SECRET

# Start the database and worker
docker compose up -d

# Run migrations
pip install -e ".[dev]"
alembic upgrade head

# Seed a default tenant with an API key
python -m humetric.seed --tenant default --ad "Default Tenant" --api-key admin

# Start the API server
uvicorn humetric.api:app --reload --port 8002
```

API is now available at `http://localhost:8002`. Swagger docs at `http://localhost:8002/docs`.

### Basic Workflow

```bash
BASE="http://localhost:8002/v1"
KEY="hm_live_YOUR_API_KEY"

# 1. Upload a Metric Pack
curl -X POST "$BASE/packs" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"yaml_text": "...", "pack_key": "my-domain"}'

# 2. Create an entity
curl -X POST "$BASE/entities" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"id": "worker-001", "entity_type": "worker", "fields": {"region": "Istanbul"}}'

# 3. Send a signal (queued, processed asynchronously)
curl -X POST "$BASE/signals" \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: signal-2025-001" \
  -d '{"entity_id": "worker-001", "entity_type": "worker", "text": "Arrived 10 min early for a difficult repair. Customer praised the clean work."}'

# 4. Check the entity's metrics
curl "$BASE/entities/worker-001" -H "Authorization: Bearer $KEY"

# 5. Query: find the most punctual workers in Istanbul
curl -X POST "$BASE/query" \
  -H "Authorization: Bearer $KEY" \
  -d '{"entity_type": "worker", "rank_by": "punctuality", "filters": {"region": "Istanbul"}, "top_k": 5}'
```

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  REST API   │────▶│  Task Queue  │────▶│  Worker Process  │
│  (FastAPI)  │     │  (PostgreSQL)│     │  (async loop)    │
└──────┬──────┘     └──────────────┘     └────────┬────────┘
       │                                          │
       │  ┌────────────┐              ┌───────────▼───────────┐
       └─▶│ PostgreSQL │◀─────────────│  Agent Pipeline        │
          │ + pgvector │              │  Extract → Curate     │
          │ + RLS      │              │  Re-embed → Complete   │
          └─────┬──────┘              └───────────────────────┘
                │
    ┌───────────┴───────────┐
    │  Anthropic (LLM)      │
    │  Voyage AI (Embedding)│
    └───────────────────────┘
```

| Layer | Technology |
|-------|-----------|
| API | FastAPI (async), Uvicorn |
| Database | PostgreSQL 16 + pgvector |
| ORM | SQLAlchemy 2.0 (async) |
| Auth | Bearer API key (SHA-256 hashed) |
| LLM | Anthropic Claude (Haiku + Sonnet) |
| Embedding | Voyage AI / OpenAI / Cohere (abstracted) |
| Queue | PostgreSQL (SELECT FOR UPDATE SKIP LOCKED) |
| Observability | Prometheus metrics, JSONL telemetry |
| MCP | stdio + SSE transport |

## MCP Server

Integrate HuMetric directly into Claude Desktop or any MCP-compatible client:

```bash
# stdio mode (Claude Desktop)
python mcp_server.py --transport stdio

# SSE mode (remote, multi-client)
python mcp_server.py --transport sse --port 8765
```

Environment: set `HUMETRIC_MCP_API_KEY` and `HUMETRIC_BASE_URL` in `.env`.

For Claude Desktop, add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "humetric": {
      "command": "python",
      "args": ["mcp_server.py"],
      "env": {
        "HUMETRIC_MCP_API_KEY": "hm_live_...",
        "HUMETRIC_BASE_URL": "http://localhost:8002"
      }
    }
  }
}
```

Available tools: `humetric_ingest_signal`, `humetric_query_entities`, `humetric_get_entity`, `humetric_list_entities`.

## BYO-Key (Bring Your Own Keys)

Tenants can use their own Anthropic and Voyage API keys instead of the platform keys. Keys are encrypted at rest with AES-256-GCM.

```bash
# Generate encryption key
python -c "import secrets; print(secrets.token_hex(32))"

# Set in .env
HUMETRIC_ENCRYPTION_KEY=your-64-char-hex-key

# Upload keys via API
curl -X PUT "$BASE/tenant/keys" \
  -H "Authorization: Bearer $KEY" \
  -d '{"anthropic_key": "...", "voyage_key": "..."}'
```

## Embedding Provider Abstraction

Switch embedding providers without code changes:

```bash
HUMETRIC_EMBEDDING_PROVIDER=voyage   # default
HUMETRIC_EMBEDDING_PROVIDER=openai   # text-embedding-3-small (1536 dim)
HUMETRIC_EMBEDDING_PROVIDER=cohere   # embed-english-v3.0 (1024 dim)
```

All providers include built-in exponential backoff and retry logic.

## Client SDKs

Client SDKs are generated from the OpenAPI 3.1 spec for **Python,
TypeScript, .NET, PHP, and Java**. They are not yet published to package
registries — generate them yourself from the spec:

```bash
# 1. Export the OpenAPI spec
python generate_openapi.py        # writes openapi.json

# 2. Generate a client with openapi-generator, e.g. Python
npx @openapitools/openapi-generator-cli generate \
  -i openapi.json \
  -g python \
  -c openapi-generator-config/python.yaml \
  -o ./humetric-sdk-python
```

Per-language generator configs live in `openapi-generator-config/`
(`python`, `typescript`, `dotnet`, `php`, `java`). The
[`SDK Generate`](.github/workflows/sdk-generate.yml) GitHub Actions workflow
(manual `workflow_dispatch`) builds all five clients from the spec.

## API Overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| **Packs** | | |
| POST | `/v1/packs` | Create or update a Metric Pack |
| GET | `/v1/packs` | List Metric Packs |
| POST | `/v1/packs/wizard` | AI-generated pack from description |
| GET | `/v1/packs/{pack_key}` | Get a Metric Pack |
| PUT | `/v1/packs/{pack_key}` | Update a Metric Pack |
| **Entities** | | |
| POST | `/v1/entities` | Create or upsert an entity |
| GET | `/v1/entities/{id}` | Get entity with metrics |
| GET | `/v1/entities/{id}/metrics` | Get entity metrics only |
| **Signals** | | |
| POST | `/v1/signals` | Submit a signal for processing |
| GET | `/v1/signals/{id}` | Check signal processing status |
| GET | `/v1/signals/{id}/trace` | Get full extraction/curation trace |
| **Query** | | |
| POST | `/v1/query` | Hybrid search + ranked entity query |
| **Metrics review** | | |
| GET | `/v1/metrics/pending-review` | List metrics awaiting review |
| PUT | `/v1/metrics/{entity_id}/{metric_key}/review` | Approve/reject a metric |
| **API keys** | | |
| POST | `/v1/api-keys` | Create scoped API key |
| GET | `/v1/api-keys` | List API keys |
| DELETE | `/v1/api-keys/{key_id}` | Revoke an API key |
| **Consent (KVKK/GDPR)** | | |
| POST | `/v1/consent` | Grant consent for sensitive metrics |
| GET | `/v1/consent/{entity_id}` | Get consent state for an entity |
| DELETE | `/v1/consent/{entity_id}` | Revoke consent |
| **Tenant & BYO keys** | | |
| GET | `/v1/tenant/keys` | Get BYO provider key status |
| PUT | `/v1/tenant/keys` | Set BYO Anthropic/Voyage keys |
| DELETE | `/v1/tenant/keys` | Remove BYO keys |
| GET | `/v1/tenant/dashboard` | Usage and subscription status |
| POST | `/v1/tenant/rotate-api-key` | Rotate default API key |
| **Billing** | | |
| POST | `/v1/billing/checkout` | Create a Stripe checkout session |
| POST | `/v1/billing/webhook` | Stripe webhook receiver |
| **Auth & registration** | | |
| POST | `/v1/register` | Register a new tenant |
| GET | `/v1/verify-email` | Verify a registration email |
| POST | `/v1/login` | Tenant login |
| **Usage & audit** | | |
| GET | `/v1/usage` | Get usage report |
| GET | `/v1/admin/usage` | Admin usage report (all tenants) |
| GET | `/v1/audit-logs` | Read audit log events |
| **Health** | | |
| GET | `/healthz` | Liveness probe |
| GET | `/healthz/db` | Database readiness probe |
| GET | `/healthz/worker` | Worker heartbeat probe |

Full OpenAPI docs at `http://localhost:8002/docs`. Postman collection at `postman/humetric-collection.json`.

## Configuration

All settings via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Anthropic API key (required) |
| `VOYAGE_API_KEY` | — | Voyage AI API key (required) |
| `DATABASE_URL` | — | PostgreSQL connection string |
| `HUMETRIC_AUTH_SECRET` | — | Secret for auth tokens |
| `HUMETRIC_EMBEDDING_PROVIDER` | `voyage` | `voyage`, `openai`, or `cohere` |
| `HUMETRIC_CONFIDENCE_THRESHOLD` | `0.55` | Minimum confidence threshold |
| `HUMETRIC_DECAY_LAMBDA` | `ln(2)/365` | Temporal decay rate |
| `HUMETRIC_WORKER_POLL_INTERVAL_S` | `1` | Worker poll interval (seconds) |
| `HUMETRIC_WORKER_BATCH_SIZE` | `5` | Tasks per worker batch |
| `HUMETRIC_API_PORT` | `8002` | API listen port |
| `HUMETRIC_RATE_LIMIT` | `100` | Requests per minute per API key |

## Development

```bash
pip install -e ".[dev]"
docker compose up -d        # PostgreSQL + worker
alembic upgrade head        # Run migrations
pytest                      # Run tests (written locally — see CONTRIBUTING.md)
```

## Deployment

```bash
# Production
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Worker runs alongside the database; scale horizontally by adding more worker replicas. The task queue uses PostgreSQL advisory locking for safe concurrent consumption.

## Contributing

Contributions are welcome! Please read our
[Contributing Guide](CONTRIBUTING.md) to get started — it covers the
development setup, coding conventions, commit format, and the pull request
process.

- 🐛 **Found a bug or have an idea?** Open an
  [issue](https://github.com/bestekarx/humetric/issues/new/choose).
- 💬 **Questions or discussion?** Use
  [GitHub Discussions](https://github.com/bestekarx/humetric/discussions).
- 🔒 **Security vulnerability?** Please follow our
  [Security Policy](SECURITY.md) — do not open a public issue.
- 🤝 By participating you agree to our
  [Code of Conduct](CODE_OF_CONDUCT.md).

New contributors: look for issues labelled
[`good first issue`](https://github.com/bestekarx/humetric/labels/good%20first%20issue).

## License

[MIT](LICENSE)
