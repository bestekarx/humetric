"""Store katmani testleri."""

import pytest

from humetric.store import Store


@pytest.mark.asyncio
async def test_create_tenant(test_db):
    store = Store()
    tenant = await store.create_tenant(test_db, {
        "kod": "store-test-tenant",
        "ad": "Store Test",
    })
    assert tenant.id is not None
    assert tenant.kod == "store-test-tenant"


@pytest.mark.asyncio
async def test_upsert_entity_creates_and_updates(test_db, test_tenant):
    store = Store()

    entity = await store.upsert_entity(test_db, {
        "id": "store-entity",
        "tenant_id": test_tenant.id,
        "entity_type": "type_a",
        "fields": {"x": 1},
    })
    assert entity.entity_type == "type_a"
    assert entity.fields == {"x": 1}

    entity = await store.upsert_entity(test_db, {
        "id": "store-entity",
        "tenant_id": test_tenant.id,
        "entity_type": "type_a",
        "fields": {"x": 2, "y": 3},
    })
    assert entity.fields == {"x": 2, "y": 3}


@pytest.mark.asyncio
async def test_create_api_key_returns_valid_key(test_db, test_tenant):
    store = Store()
    full_key, api_key = await store.create_api_key(
        test_db,
        tenant_id=test_tenant.id,
        prefix="hm_test",
        label="Test Key",
        scopes=["signals:write", "entities:read"],
    )
    assert full_key.startswith("hm_test_")
    assert api_key.label == "Test Key"
    assert "signals:write" in api_key.scopes


@pytest.mark.asyncio
async def test_verify_and_get_api_key(test_db, test_tenant):
    store = Store()
    full_key, _ = await store.create_api_key(
        test_db,
        tenant_id=test_tenant.id,
        prefix="hm_live",
        label="Verify Test",
        scopes=["query"],
    )

    verified = await store.verify_and_get_api_key(test_db, full_key)
    assert verified is not None
    assert verified.label == "Verify Test"

    bad = await store.verify_and_get_api_key(test_db, "hm_live_invalid_key")
    assert bad is None


@pytest.mark.asyncio
async def test_revoke_api_key(test_db, test_tenant):
    store = Store()
    full_key, api_key = await store.create_api_key(
        test_db,
        tenant_id=test_tenant.id,
        prefix="hm_test",
        label="Revoke Test",
        scopes=["entities:read"],
    )

    assert await store.revoke_api_key(test_db, api_key.id) is True
    assert await store.revoke_api_key(test_db, 99999) is False

    verified = await store.verify_and_get_api_key(test_db, full_key)
    assert verified is None


@pytest.mark.asyncio
async def test_list_api_keys(test_db, test_tenant):
    store = Store()
    await store.create_api_key(test_db, tenant_id=test_tenant.id, prefix="hm_test", label="K1", scopes=["query"])
    await store.create_api_key(test_db, tenant_id=test_tenant.id, prefix="hm_test", label="K2", scopes=["entities:read"])

    keys = await store.list_api_keys(test_db, test_tenant.id)
    labels = [k.label for k in keys if k.label in ("K1", "K2")]
    assert len(labels) >= 2
