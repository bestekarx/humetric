# HuMetric External Platform

Domain-agnostic entity metric engine — REST API + MCP + SDK with multi-tenant isolation.

## Quickstart

```bash
cd humetric
cp .env.example .env
docker compose up -d
alembic upgrade head
python -m humetric.seed --tenant default --ad "Default Tenant" --api-key admin
uvicorn humetric.api:app --reload --port 8002
```

See [quickstart.md](../specs/021-humetric-platform-init/quickstart.md) for detailed setup.

## Architecture

Humetric turns free-text signals about entities (workers, dealers, regions...) into calibrated, decaying metrics over time via LLM extraction + curator validation. The platform is domain-agnostic: any industry is defined via a Metric Pack (YAML), no code changes required.

### Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI (async), Uvicorn |
| Database | PostgreSQL 15 + pgvector |
| ORM | SQLAlchemy 2.0 (async) |
| Auth | API key (SHA-256 hashed) |
| LLM | Anthropic Claude (Haiku + Sonnet) |
| Embedding | Voyage AI (abstracted provider) |
| KVKK/GDPR | 3-layer filter (DB + model + embedding) |

### Multi-tenant Isolation

Row-Level Security (RLS) via PostgreSQL. Each API key resolves to a tenant; `set_config('app.tenant_id', ...)` activates the isolation policy. Fail-closed: no GUC → zero rows returned.

## Phase Plan

| Phase | Spec | Description |
|-------|------|-------------|
| 0 | 021 | Project skeleton, Docker, DB schema (this) |
| 1 | 022 | REST API core endpoints, auth, tenant isolation |
| 2 | 023 | Metric Pack engine, AI pack wizard, KVKK pack-driven |
| 3 | 024 | Task queue, async processing, embedding abstraction |
| 4 | 025 | OpenAPI 3.1, SDK automation (5 languages), docs portal |
| 5 | 026 | MCP server, production deploy, CI/CD, marketplace |
| — | 027 | Gap completion: pack samples, prompts, Postman, tests |

## Sample Metric Packs

Ready-to-use Metric Pack YAML files in `packs/`:

| File | Entity Type | Domain |
|------|-------------|--------|
| `saha-hizmet-isci.yaml` | isci | Field service worker (dakiklik, titizlik, teknik_beceri, iletisim, mali_durum) |
| `saha-hizmet-cari.yaml` | cari | Customer/firm (odeme_disiplini, is_hacmi, guvenilirlik) |
| `lastik-bayi.yaml` | bayi | Tire distribution dealer (satis_performansi, tahsilat_disiplini, musteri_memnuniyeti) |
| `lastik-bolge-sorumlusu.yaml` | bolge_sorumlusu | Regional manager (bayi_yonetimi, raporlama, saha_denetimi) |

Upload via `POST /v1/packs` with the YAML content.

## Agent Prompts

Agent system prompts are externalized as `.md` files in `prompts/`. Agents load prompts via 3-tier fallback: pack definition → `prompts/{agent}-default.md` → minimal inline.

| File | Agent |
|------|-------|
| `prompts/extractor-default.md` | Signal metric extractor (Haiku) |
| `prompts/curator-default.md` | Metric curator/validator (Sonnet) |
| `prompts/ranker-default.md` | Entity ranker (Sonnet) |
| `prompts/wizard-system.md` | AI pack wizard (Haiku) |

## Postman Collection

Import `postman/humetric-collection.json` (23 requests across 12 folders). Set `baseUrl` and `apiKey` variables.

## BYO-Key (Spec 025)

Tenants can bring their own Anthropic and Voyage API keys. Keys are encrypted at rest with AES-256-GCM before storage.

### Configuration

Set `HUMETRIC_ENCRYPTION_KEY` in `.env` to a 32-byte hex string:

```
HUMETRIC_ENCRYPTION_KEY=abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789
```

Generate with: `python -c "import secrets; print(secrets.token_hex(32))"`

### API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/tenant/keys` | Check which BYO keys are stored |
| PUT | `/v1/tenant/keys` | Upload Anthropic/Voyage API keys |
| DELETE | `/v1/tenant/keys` | Remove all stored keys |

Without `HUMETRIC_ENCRYPTION_KEY`, BYO-key endpoints return `501 Not Implemented`.

## SDK (Spec 025)

Auto-generated SDKs from the OpenAPI 3.1 spec using OpenAPI Generator:

| Language | Package |
|----------|---------|
| Python | `pip install humetric-sdk` |
| TypeScript | `npm install @humetric/sdk` |
| Go | `go get github.com/humetric/humetric-go` |
| Java | Maven `com.humetric:humetric-client` |
| Ruby | `gem install humetric-sdk` |

SDK sources are generated into `sdk-templates/`. See `openapi-generator-config/` for generator configs and `scripts/generate-sdks.sh` for the automation script.

## Development

```bash
pip install -e ".[dev]"
docker compose up -d
alembic upgrade head
pytest
```
