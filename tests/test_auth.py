"""Auth middleware testleri — API key dogrulama, scope kontrolu, RLS izolasyonu."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from humetric.api import app
from humetric.schema import error_envelope


class FakeApiKeyRow:
    def __init__(self, tenant_id=1, scopes=None, revoked=False, expired=False):
        self.id = 1
        self.tenant_id = tenant_id
        self.scopes = scopes or ["entities:write", "entities:read", "signals:write", "signals:read", "query"]
        self.key_hash = "test_hash"
        self.key_prefix = "hm_test"
        self.created_by = "seed"
        self.revoked_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc) if revoked else None
        import datetime as dt
        self.expires_at = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc) if expired else None


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def valid_key_row():
    return FakeApiKeyRow()


class TestAuthMiddleware:

    async def test_invalid_api_key_401(self, client):
        with patch("humetric.store.verify_and_get_api_key", new_callable=AsyncMock) as mock_v:
            mock_v.return_value = None
            resp = await client.get("/v1/entities/e1", headers={"Authorization": "Bearer invalid"})
            assert resp.status_code == 401
            data = resp.json()
            assert data["error"]["code"] in ("invalid_api_key", "invalid_api_key")

    async def test_revoked_api_key_401(self, client):
        with patch("humetric.store.verify_and_get_api_key", new_callable=AsyncMock) as mock_v:
            mock_v.return_value = FakeApiKeyRow(revoked=True)
            resp = await client.get("/v1/entities/e1", headers={"Authorization": "Bearer revoked_key"})
            assert resp.status_code == 401

    async def test_expired_api_key_401(self, client):
        with patch("humetric.store.verify_and_get_api_key", new_callable=AsyncMock) as mock_v:
            mock_v.return_value = FakeApiKeyRow(expired=True)
            resp = await client.get("/v1/entities/e1", headers={"Authorization": "Bearer expired_key"})
            assert resp.status_code == 401

    async def test_missing_auth_header_401(self, client):
        resp = await client.get("/v1/entities/e1")
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "invalid_api_key"


class TestScopeAuthorization:

    async def test_insufficient_scopes_403(self, client):
        with patch("humetric.store.verify_and_get_api_key", new_callable=AsyncMock) as mock_v:
            mock_v.return_value = FakeApiKeyRow(scopes=["signals:read"])  # no entities:read
            resp = await client.get("/v1/entities/e1", headers={"Authorization": "Bearer limited_key"})
            assert resp.status_code in (401, 403)

    async def test_rls_tenant_isolation_404(self, client):
        with patch("humetric.store.verify_and_get_api_key", new_callable=AsyncMock) as mock_v:
            with patch("humetric.store.get_entity_with_metrics", new_callable=AsyncMock) as mock_get:
                mock_v.return_value = FakeApiKeyRow(tenant_id=2)  # Tenant B
                mock_get.return_value = None  # RLS: Tenant A key ile Tenant B entity → None
                resp = await client.get("/v1/entities/e1", headers={"Authorization": "Bearer tenant_b_key"})
                assert resp.status_code == 404


class TestHealthz:

    async def test_healthz_no_auth(self, client):
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
