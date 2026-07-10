"""Consent endpoint testleri (Spec 023)."""

from datetime import datetime, timedelta, timezone

import pytest

from humetric import kvkk
from humetric.store import Store


SENSITIVE_PACK_YAML = """entity_type: consent_test
label: "Consent Test"
version: 1
required_fields: []
metrics:
  - key: performans
    label: "Performans"
    type: float
    prompt: "Performans?"
    default_confidence: 0.7
    sensitive: false
  - key: saglik_durumu
    label: "Saglik Durumu"
    type: float
    prompt: "Saglik?"
    default_confidence: 0.8
    sensitive: true
    visible_to: [entities:write, entities:read]
    requires_consent_scope: saglik_verisi
prompts:
  extraction: "Cikar."
  curation: "Dogrula."
kvkk:
  sensitive_metrics:
    - saglik_durumu
"""


@pytest.mark.asyncio
async def test_create_consent_201(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": SENSITIVE_PACK_YAML, "pack_key": "consent-pack"},
        headers=headers,
    )
    await async_client.post(
        "/v1/entities",
        json={"id": "consent-entity", "entity_type": "consent_test"},
        headers=headers,
    )
    res = await async_client.post(
        "/v1/consent",
        json={"entity_id": "consent-entity", "scope": "saglik_verisi"},
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["scope"] == "saglik_verisi"
    assert data["status"] == "granted"


@pytest.mark.asyncio
async def test_create_consent_entity_not_found_404(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    res = await async_client.post(
        "/v1/consent",
        json={"entity_id": "nonexistent", "scope": "test"},
        headers=headers,
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_get_consents_200(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": SENSITIVE_PACK_YAML, "pack_key": "consent-pack-2"},
        headers=headers,
    )
    await async_client.post(
        "/v1/entities",
        json={"id": "consent-entity-2", "entity_type": "consent_test"},
        headers=headers,
    )
    await async_client.post(
        "/v1/consent",
        json={"entity_id": "consent-entity-2", "scope": "saglik_verisi"},
        headers=headers,
    )
    res = await async_client.get("/v1/consent/consent-entity-2", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["scope"] == "saglik_verisi"


@pytest.mark.asyncio
async def test_revoke_consent_200(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": SENSITIVE_PACK_YAML, "pack_key": "consent-pack-3"},
        headers=headers,
    )
    await async_client.post(
        "/v1/entities",
        json={"id": "consent-entity-3", "entity_type": "consent_test"},
        headers=headers,
    )
    await async_client.post(
        "/v1/consent",
        json={"entity_id": "consent-entity-3", "scope": "saglik_verisi"},
        headers=headers,
    )
    res = await async_client.delete(
        "/v1/consent/consent-entity-3?scope=saglik_verisi",
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["revoked"] is True


@pytest.mark.asyncio
async def test_revoke_all_scopes_200(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": SENSITIVE_PACK_YAML, "pack_key": "consent-pack-4"},
        headers=headers,
    )
    await async_client.post(
        "/v1/entities",
        json={"id": "consent-entity-4", "entity_type": "consent_test"},
        headers=headers,
    )
    await async_client.post(
        "/v1/consent",
        json={"entity_id": "consent-entity-4", "scope": "saglik_verisi"},
        headers=headers,
    )
    await async_client.post(
        "/v1/consent",
        json={"entity_id": "consent-entity-4", "scope": "finansal_veri"},
        headers=headers,
    )
    res = await async_client.delete(
        "/v1/consent/consent-entity-4",
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["revoked"] is True
    assert data["scope"] == "all"


@pytest.mark.asyncio
async def test_consent_unauthorized_403(async_client):
    headers = {"Authorization": "Bearer invalid_key_12345"}
    res = await async_client.post(
        "/v1/consent",
        json={"entity_id": "x", "scope": "test"},
        headers=headers,
    )
    assert res.status_code == 401


# ── Consent logic tests (DB-level, no API) ──────────────────────

@pytest.mark.asyncio
async def test_consent_check_granted(test_db, test_tenant):
    store = Store()
    await store.create_entity(test_db, {
        "id": "ct-entity",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })
    await store.create_consent(test_db, {
        "tenant_id": test_tenant.id,
        "entity_id": "ct-entity",
        "scope": "saglik_verisi",
        "status": "granted",
    })
    assert await store.check_consent(test_db, "ct-entity", "saglik_verisi", test_tenant.id) is True


@pytest.mark.asyncio
async def test_consent_check_revoked(test_db, test_tenant):
    store = Store()
    await store.create_entity(test_db, {
        "id": "ct-revoked",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })
    await store.create_consent(test_db, {
        "tenant_id": test_tenant.id,
        "entity_id": "ct-revoked",
        "scope": "saglik_verisi",
        "status": "granted",
    })
    await store.revoke_consent(test_db, "ct-revoked", "saglik_verisi", test_tenant.id)
    assert await store.check_consent(test_db, "ct-revoked", "saglik_verisi", test_tenant.id) is False


@pytest.mark.asyncio
async def test_consent_check_expired(test_db, test_tenant):
    store = Store()
    await store.create_entity(test_db, {
        "id": "ct-expired",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })
    await store.create_consent(test_db, {
        "tenant_id": test_tenant.id,
        "entity_id": "ct-expired",
        "scope": "saglik_verisi",
        "status": "granted",
        "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
    })
    assert await store.check_consent(test_db, "ct-expired", "saglik_verisi", test_tenant.id) is False


@pytest.mark.asyncio
async def test_consent_check_not_expired(test_db, test_tenant):
    store = Store()
    await store.create_entity(test_db, {
        "id": "ct-notexpired",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })
    await store.create_consent(test_db, {
        "tenant_id": test_tenant.id,
        "entity_id": "ct-notexpired",
        "scope": "saglik_verisi",
        "status": "granted",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
    })
    assert await store.check_consent(test_db, "ct-notexpired", "saglik_verisi", test_tenant.id) is True


@pytest.mark.asyncio
async def test_consent_check_no_expiry(test_db, test_tenant):
    store = Store()
    await store.create_entity(test_db, {
        "id": "ct-noexpiry",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })
    await store.create_consent(test_db, {
        "tenant_id": test_tenant.id,
        "entity_id": "ct-noexpiry",
        "scope": "saglik_verisi",
        "status": "granted",
    })
    assert await store.check_consent(test_db, "ct-noexpiry", "saglik_verisi", test_tenant.id) is True


@pytest.mark.asyncio
async def test_kvkk_check_consent_for_metric_granted(test_db, test_tenant):
    store = Store()
    await store.create_entity(test_db, {
        "id": "kvkk-metric-entity",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })
    await store.create_consent(test_db, {
        "tenant_id": test_tenant.id,
        "entity_id": "kvkk-metric-entity",
        "scope": "saglik_verisi",
        "status": "granted",
    })
    result = await kvkk.check_consent_for_metric(
        test_db, "kvkk-metric-entity", "saglik_verisi", test_tenant.id,
    )
    assert result is True


@pytest.mark.asyncio
async def test_kvkk_check_consent_for_metric_no_scope(test_db, test_tenant):
    result = await kvkk.check_consent_for_metric(
        test_db, "any-entity", None, test_tenant.id,
    )
    assert result is True


@pytest.mark.asyncio
async def test_kvkk_check_consent_for_metric_revoked(test_db, test_tenant):
    store = Store()
    await store.create_entity(test_db, {
        "id": "kvkk-metric-rev",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })
    await store.create_consent(test_db, {
        "tenant_id": test_tenant.id,
        "entity_id": "kvkk-metric-rev",
        "scope": "saglik_verisi",
        "status": "granted",
    })
    await store.revoke_consent(test_db, "kvkk-metric-rev", "saglik_verisi", test_tenant.id)
    result = await kvkk.check_consent_for_metric(
        test_db, "kvkk-metric-rev", "saglik_verisi", test_tenant.id,
    )
    assert result is False
