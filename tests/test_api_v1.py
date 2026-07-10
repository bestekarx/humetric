"""API V1 endpoint testleri — httpx AsyncClient ile.

Gerçek DB yerine mock store kullanir; middleware atlanir.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from humetric.api import app
from humetric.schema import (
    EntityMetric,
    EntityResponse,
    SignalResult,
    SignalStatus,
    ApiKeyCreated,
    ApiKeyInfo,
    ApiKeyList,
    QueryResponse,
    RankedResult,
)


def _now():
    return datetime.now(timezone.utc)


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def seed_key():
    return "hm_test_seed_key_abc123"


@pytest.fixture
def auth_headers(seed_key):
    return {"Authorization": f"Bearer {seed_key}"}


@pytest.fixture
def mock_store():
    return {
        "entities": {},
        "signals": {},
        "api_keys": {},
        "metrics": {},
    }


@pytest.fixture
async def client(mock_store):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Helper: mock the auth middleware and store dependencies ─────

class FakeApiKeyRow:
    def __init__(self):
        self.id = 1
        self.tenant_id = 1
        self.scopes = [
            "entities:write", "entities:read",
            "signals:write", "signals:read",
            "query",
        ]
        self.key_hash = "test_hash"
        self.key_prefix = "hm_test"
        self.created_by = "seed"
        self.revoked_at = None
        self.expires_at = None


fake_key = FakeApiKeyRow()


async def fake_verify(key_str):
    return fake_key


@pytest.fixture(autouse=True)
def patch_auth_and_store(mock_store):
    import humetric.middleware.auth as auth_mod
    import humetric.store as store_mod
    from humetric.db.engine import get_tenant_db as real_get_tenant_db

    with patch.object(store_mod, "verify_and_get_api_key", side_effect=fake_verify):
        with patch.object(store_mod, "upsert_entity", new_callable=AsyncMock) as mock_upsert:
            with patch.object(store_mod, "get_entity", new_callable=AsyncMock) as mock_get:
                with patch.object(store_mod, "get_entity_with_metrics", new_callable=AsyncMock) as mock_get_wm:
                    with patch.object(store_mod, "get_entity_metrics", new_callable=AsyncMock) as mock_get_m:
                        with patch.object(store_mod, "create_signal", new_callable=AsyncMock) as mock_create_sig:
                            with patch.object(store_mod, "get_signal", new_callable=AsyncMock) as mock_get_sig:
                                with patch.object(store_mod, "update_signal_status", new_callable=AsyncMock) as mock_upd_sig:
                                    with patch.object(store_mod, "create_api_key", new_callable=AsyncMock) as mock_create_key:
                                        with patch.object(store_mod, "list_api_keys", new_callable=AsyncMock) as mock_list_keys:
                                            with patch.object(store_mod, "revoke_api_key", new_callable=AsyncMock) as mock_revoke:
                                                with patch.object(store_mod, "upsert_metric", new_callable=AsyncMock) as mock_upsert_m:
                                                    with patch.object(store_mod, "update_entity_embedding", new_callable=AsyncMock) as mock_re_embed:
                                                        with patch("humetric.rag.hybrid_search", new_callable=AsyncMock) as mock_search:
                                                            with patch("humetric.api.extractor.extract_metrics", new_callable=AsyncMock) as mock_extract:
                                                                with patch("humetric.api.curator.curate_metrics", new_callable=AsyncMock) as mock_curate:
                                                                    with patch("humetric.api.ranker.rank_entities", new_callable=AsyncMock) as mock_rank:
                                                                        with patch.object(auth_mod.AuthMiddleware, "dispatch", side_effect=_fake_auth_dispatch):
                                                                            # Store all for use in tests
                                                                            mock_store["_mocks"] = {
                                                                                "upsert_entity": mock_upsert,
                                                                                "get_entity": mock_get,
                                                                                "get_entity_with_metrics": mock_get_wm,
                                                                                "get_entity_metrics": mock_get_m,
                                                                                "create_signal": mock_create_sig,
                                                                                "get_signal": mock_get_sig,
                                                                                "update_signal_status": mock_upd_sig,
                                                                                "create_api_key": mock_create_key,
                                                                                "list_api_keys": mock_list_keys,
                                                                                "revoke_api_key": mock_revoke,
                                                                                "upsert_metric": mock_upsert_m,
                                                                                "update_entity_embedding": mock_re_embed,
                                                                                "hybrid_search": mock_search,
                                                                                "extract": mock_extract,
                                                                                "curate": mock_curate,
                                                                                "rank": mock_rank,
                                                                            }
                                                                            yield mock_store


async def _fake_auth_dispatch(request, call_next):
    request.state.api_key_id = 1
    request.state.tenant_id = 1
    request.state.scopes = [
        "entities:write", "entities:read",
        "signals:write", "signals:read",
        "query",
    ]
    return await call_next(request)


# ── Helper factories ───────────────────────────────────────────

def _entity_row(ident="e1", etype="worker", fields=None, free_text="", status="active"):
    class FakeRow:
        id = ident
        entity_type = etype
        fields = fields or {}
        free_text = free_text
        status = status
        metrics = []
        created_at = _now()
        updated_at = _now()
        tenant_id = 1
        embedding = None
        embedding_metni = None
        meta = {}
    return FakeRow()


def _signal_row(sid="s1", eid="e1", etype="worker", text="test", status="completed"):
    class FakeRow:
        id = sid
        entity_id = eid
        entity_type = etype
        text = text
        structured = {}
        status = status
        result = {}
        error = None
        retry_count = 0
        max_retries = 3
        created_at = _now()
        processed_at = _now()
        tenant_id = 1
    return FakeRow()


# ── Tests ─────────────────────────────────────────────────────

class TestEntityEndpoints:

    async def test_create_entity_201(self, client, mock_store):
        mock_store["_mocks"]["upsert_entity"].return_value = (_entity_row(), True)
        resp = await client.post("/v1/entities", json={
            "id": "e1", "entity_type": "worker", "fields": {"name": "Test"}
        })
        assert resp.status_code in (200, 201)

    async def test_get_entity_200(self, client, mock_store):
        row = _entity_row("e1")
        mock_store["_mocks"]["get_entity_with_metrics"].return_value = row
        resp = await client.get("/v1/entities/e1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "e1"

    async def test_get_entity_404(self, client, mock_store):
        mock_store["_mocks"]["get_entity_with_metrics"].return_value = None
        resp = await client.get("/v1/entities/nonexistent")
        assert resp.status_code == 404

    async def test_get_entity_metrics(self, client, mock_store):
        row = _entity_row("e1")
        class FakeMetric:
            metric_key = "score"
            value = 0.8
            confidence = 0.9
            reasoning = "test"
            source_signal_id = None
            updated_at = _now()
        row.metrics = [FakeMetric()]
        mock_store["_mocks"]["get_entity"].return_value = row
        resp = await client.get("/v1/entities/e1/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["entity_id"] == "e1"
        assert data["metric_count"] >= 0


class TestSignalEndpoints:

    async def test_create_signal_200(self, client, mock_store):
        row = _entity_row("e1")
        mock_store["_mocks"]["get_entity"].return_value = row

        sig_row = _signal_row()
        mock_store["_mocks"]["create_signal"].return_value = sig_row

        from humetric.schema import ExtractedMetric, FinalMetric
        mock_store["_mocks"]["extract"].return_value = [
            ExtractedMetric(metric_key="perf", value=0.8, confidence=0.9, reasoning="good")
        ]
        mock_store["_mocks"]["curate"].return_value = [
            FinalMetric(metric_key="perf", value=0.8, confidence=0.9, reasoning="good")
        ]

        resp = await client.post("/v1/signals", json={
            "entity_id": "e1", "entity_type": "worker",
            "text": "Excellent performance"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert "signal_id" in data

    async def test_signal_archived_entity_403(self, client, mock_store):
        row = _entity_row("e1", status="archived")
        mock_store["_mocks"]["get_entity"].return_value = row
        resp = await client.post("/v1/signals", json={
            "entity_id": "e1", "entity_type": "worker", "text": "test"
        })
        assert resp.status_code == 403

    async def test_get_signal_status(self, client, mock_store):
        row = _signal_row("s1", status="completed")
        mock_store["_mocks"]["get_signal"].return_value = row
        resp = await client.get("/v1/signals/s1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"

    async def test_get_signal_trace(self, client, mock_store):
        row = _signal_row("s1", status="completed")
        row.result = {"metrics": [{"metric_key": "perf", "value": 0.8}]}
        mock_store["_mocks"]["get_signal"].return_value = row

        ent_row = _entity_row("e1")
        mock_store["_mocks"]["get_entity"].return_value = ent_row

        resp = await client.get("/v1/signals/s1/trace")
        assert resp.status_code == 200
        data = resp.json()
        assert data["signal_id"] == "s1"


class TestApiKeyEndpoints:

    async def test_create_api_key_201(self, client, mock_store):
        class FakeKeyRow:
            id = 1
            name = "test-key"
            key_prefix = "hm_test"
            scopes = ["entities:write"]
            expires_at = None
        mock_store["_mocks"]["create_api_key"].return_value = (FakeKeyRow(), "hm_full_key_123")
        resp = await client.post("/v1/api-keys", json={"name": "test-key", "scopes": ["entities:write"]})
        assert resp.status_code == 201
        data = resp.json()
        assert "full_key" in data

    async def test_list_api_keys(self, client, mock_store):
        class FakeKeyRow:
            id = 1
            name = "k1"
            key_prefix = "hm_x"
            scopes = ["query"]
            created_by = "1"
            expires_at = None
            revoked_at = None
            created_at = _now()
        mock_store["_mocks"]["list_api_keys"].return_value = [FakeKeyRow()]
        resp = await client.get("/v1/api-keys")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["api_keys"]) >= 1

    async def test_delete_api_key(self, client, mock_store):
        class FakeKeyRow:
            id = 1
            revoked_at = None
        mock_store["_mocks"]["revoke_api_key"].return_value = FakeKeyRow()
        resp = await client.delete("/v1/api-keys/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "revoked"


class TestQueryEndpoint:

    async def test_query_returns_results(self, client, mock_store):
        mock_store["_mocks"]["hybrid_search"].return_value = [_entity_row("e1")]
        from humetric.schema import RankedResult
        mock_store["_mocks"]["rank"].return_value = [
            RankedResult(entity_id="e1", entity_type="worker", score=0.9, reasoning="good")
        ]
        ent = _entity_row("e1")
        class FakeMetric:
            metric_key = "perf"
            value = 0.8
            confidence = 0.9
            reasoning = "test"
            source_signal_id = None
            updated_at = _now()
        ent.metrics = [FakeMetric()]
        mock_store["_mocks"]["get_entity"].return_value = ent

        resp = await client.post("/v1/query", json={
            "free_text_query": "good worker", "top_k": 5
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) >= 1

    async def test_query_empty_result(self, client, mock_store):
        mock_store["_mocks"]["hybrid_search"].return_value = []
        mock_store["_mocks"]["rank"].return_value = []
        resp = await client.post("/v1/query", json={
            "free_text_query": "nonexistent"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []

    async def test_error_response_envelope(self, client, mock_store):
        resp = await client.get("/v1/entities/nonexistent")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
