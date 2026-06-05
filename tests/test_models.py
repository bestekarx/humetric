"""Veri modeli CRUD + RLS izolasyon testleri."""

import pytest
from sqlalchemy import select

from humetric.db.models import ApiKey, AuditLog, Consent, Entity, EntityMetric, Tenant
from humetric.store import Store


@pytest.mark.asyncio
async def test_tenant_crud(test_db):
    store = Store()
    tenant = await store.create_tenant(test_db, {
        "kod": "crud-test",
        "ad": "CRUD Test Tenant",
    })
    assert tenant.id is not None
    assert tenant.kod == "crud-test"

    found = await store.get_tenant_by_kod(test_db, "crud-test")
    assert found is not None
    assert found.ad == "CRUD Test Tenant"


@pytest.mark.asyncio
async def test_entity_crud(test_db, test_tenant):
    store = Store()
    entity = await store.create_entity(test_db, {
        "id": "entity-1",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
        "fields": {"key": "value"},
    })
    assert entity.id == "entity-1"
    assert entity.status == "active"

    found = await store.get_entity(test_db, "entity-1", test_tenant.id)
    assert found is not None
    assert found.entity_type == "test_type"


@pytest.mark.asyncio
async def test_entity_upsert_creates_and_updates(test_db, test_tenant):
    store = Store()

    entity = await store.upsert_entity(test_db, {
        "id": "upsert-1",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
        "fields": {"v": 1},
    })
    assert entity.fields == {"v": 1}

    entity = await store.upsert_entity(test_db, {
        "id": "upsert-1",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
        "fields": {"v": 2},
        "status": "archived",
    })
    assert entity.fields == {"v": 2}
    assert entity.status == "archived"


@pytest.mark.asyncio
async def test_entity_metric_unique_constraint(test_db, test_tenant):
    store = Store()

    await store.create_entity(test_db, {
        "id": "metric-test-1",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })

    metric1 = await store.upsert_metric(test_db, {
        "entity_id": "metric-test-1",
        "tenant_id": test_tenant.id,
        "metric_key": "test_metric",
        "value": 0.5,
        "confidence": 0.8,
    })
    assert metric1.value == 0.5

    metric2 = await store.upsert_metric(test_db, {
        "entity_id": "metric-test-1",
        "tenant_id": test_tenant.id,
        "metric_key": "test_metric",
        "value": 0.9,
        "confidence": 0.7,
    })
    assert metric2.value == 0.9

    metrics = await store.get_entity_metrics(test_db, "metric-test-1", test_tenant.id)
    assert len(metrics) == 1


@pytest.mark.asyncio
async def test_api_key_hash_verify(test_db, test_tenant):
    from humetric import auth

    full_key, key_hash = auth.generate_api_key("hm_test")
    assert full_key.startswith("hm_test_")
    assert auth.verify_key(full_key, key_hash) is True
    assert auth.verify_key("wrong_key", key_hash) is False


@pytest.mark.asyncio
async def test_api_key_crud(test_db, test_tenant):
    store = Store()
    full_key, api_key = await store.create_api_key(
        test_db,
        tenant_id=test_tenant.id,
        prefix="hm_test",
        label="Test",
        scopes=["signals:write"],
    )
    assert api_key.id is not None

    verified = await store.verify_and_get_api_key(test_db, full_key)
    assert verified is not None
    assert verified.id == api_key.id

    assert verified.last_used_at is not None

    await store.revoke_api_key(test_db, api_key.id)
    revoked = await store.verify_and_get_api_key(test_db, full_key)
    assert revoked is None


@pytest.mark.asyncio
async def test_consent_crud(test_db, test_tenant):
    store = Store()

    await store.create_entity(test_db, {
        "id": "consent-entity",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })

    consent = await store.create_consent(test_db, {
        "tenant_id": test_tenant.id,
        "entity_id": "consent-entity",
        "scope": "hassas_veri_isleme",
    })
    assert consent.id is not None
    assert consent.status == "granted"

    has_consent = await store.check_consent(
        test_db, "consent-entity", "hassas_veri_isleme", test_tenant.id
    )
    assert has_consent is True

    await store.revoke_consent(test_db, "consent-entity", "hassas_veri_isleme", test_tenant.id)
    has_consent = await store.check_consent(
        test_db, "consent-entity", "hassas_veri_isleme", test_tenant.id
    )
    assert has_consent is False


@pytest.mark.asyncio
async def test_audit_log_write(test_db, test_tenant):
    store = Store()
    log = await store.write_audit_log(test_db, {
        "tenant_id": test_tenant.id,
        "action": "entity_create",
        "entity_id": "test-entity",
        "details": {"source": "test"},
    })
    assert log.id is not None
    assert log.action == "entity_create"


@pytest.mark.asyncio
async def test_rls_isolation(test_db, test_tenant):
    """Iki tenant — birinin entity'si digerinden gorunmemeli."""
    store = Store()

    tenant_b = await store.create_tenant(test_db, {"kod": "isolation-b", "ad": "Tenant B"})

    await store.create_entity(test_db, {
        "id": "tenant-a-entity",
        "tenant_id": test_tenant.id,
        "entity_type": "test_type",
    })
    await store.create_entity(test_db, {
        "id": "tenant-b-entity",
        "tenant_id": tenant_b.id,
        "entity_type": "test_type",
    })

    found_a = await store.get_entity(test_db, "tenant-a-entity", test_tenant.id)
    assert found_a is not None

    found_b = await store.get_entity(test_db, "tenant-b-entity", test_tenant.id)
    assert found_b is None
