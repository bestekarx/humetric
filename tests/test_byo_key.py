"""BYO-Key tests — Spec 025."""

import pytest
from unittest.mock import patch, AsyncMock
from humetric.store import Store, encrypt_key, decrypt_key


class TestEncryption:
    def test_encrypt_decrypt_roundtrip(self, monkeypatch):
        monkeypatch.setenv("HUMETRIC_ENCRYPTION_KEY", "a" * 64)
        import humetric.store
        humetric.store._ENCRYPTION_KEY = None

        original = "sk-ant-test-key-123"
        encrypted = encrypt_key(original)
        assert encrypted != original
        assert isinstance(encrypted, str)

        decrypted = decrypt_key(encrypted)
        assert decrypted == original

    def test_decrypt_invalid(self, monkeypatch):
        monkeypatch.setenv("HUMETRIC_ENCRYPTION_KEY", "a" * 64)
        import humetric.store
        humetric.store._ENCRYPTION_KEY = None

        result = decrypt_key("invalid-base64!!!")
        assert result is None

    def test_encrypt_without_key(self, monkeypatch):
        monkeypatch.delenv("HUMETRIC_ENCRYPTION_KEY", raising=False)
        import humetric.store
        humetric.store._ENCRYPTION_KEY = None

        with pytest.raises(RuntimeError, match="not configured"):
            encrypt_key("test")

    def test_decrypt_without_key(self, monkeypatch):
        monkeypatch.delenv("HUMETRIC_ENCRYPTION_KEY", raising=False)
        import humetric.store
        humetric.store._ENCRYPTION_KEY = None

        result = decrypt_key("dGVzdA==")
        assert result is None


class TestTenantKeysEndpoints:
    @pytest.mark.asyncio
    async def test_get_keys_empty(self, async_client, test_db, test_tenant, test_api_key):
        headers = {"Authorization": f"Bearer {test_api_key}"}
        response = await async_client.get("/v1/tenant/keys", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["has_anthropic_key"] is False
        assert data["has_voyage_key"] is False

    @pytest.mark.asyncio
    async def test_put_and_get_keys(self, async_client, test_db, test_tenant, test_api_key, monkeypatch):
        monkeypatch.setenv("HUMETRIC_ENCRYPTION_KEY", "a" * 64)
        import humetric.store
        humetric.store._ENCRYPTION_KEY = None

        headers = {"Authorization": f"Bearer {test_api_key}"}

        response = await async_client.put(
            "/v1/tenant/keys",
            headers=headers,
            json={"anthropic_key": "sk-ant-test-123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["has_anthropic_key"] is True
        assert data["has_voyage_key"] is False

        response = await async_client.get("/v1/tenant/keys", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["has_anthropic_key"] is True

    @pytest.mark.asyncio
    async def test_delete_keys(self, async_client, test_db, test_tenant, test_api_key, monkeypatch):
        monkeypatch.setenv("HUMETRIC_ENCRYPTION_KEY", "a" * 64)
        import humetric.store
        humetric.store._ENCRYPTION_KEY = None

        headers = {"Authorization": f"Bearer {test_api_key}"}

        await async_client.put(
            "/v1/tenant/keys",
            headers=headers,
            json={"anthropic_key": "sk-ant-test-123"},
        )

        response = await async_client.delete("/v1/tenant/keys", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["has_anthropic_key"] is False
        assert data["has_voyage_key"] is False

    @pytest.mark.asyncio
    async def test_put_no_encryption_key(self, async_client, test_tenant, test_api_key, monkeypatch):
        monkeypatch.delenv("HUMETRIC_ENCRYPTION_KEY", raising=False)
        import humetric.store
        humetric.store._ENCRYPTION_KEY = None

        headers = {"Authorization": f"Bearer {test_api_key}"}
        response = await async_client.put(
            "/v1/tenant/keys",
            headers=headers,
            json={"anthropic_key": "test"},
        )
        assert response.status_code == 501

    @pytest.mark.asyncio
    async def test_unauthorized(self, async_client):
        response = await async_client.get("/v1/tenant/keys")
        assert response.status_code == 401
