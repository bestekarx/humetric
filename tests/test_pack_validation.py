"""Pack-driven entity validasyon testleri (Spec 023)."""

import pytest


VALID_PACK_YAML = """entity_type: val_kurye
label: "Validation Kurye"
version: 1
required_fields:
  - bolge
  - arac_tipi
metrics:
  - key: hiz
    label: "Hiz"
    type: float
    prompt: "Hiz?"
    default_confidence: 0.7
    sensitive: false
prompts:
  extraction: "Cikar."
  curation: "Dogrula."
kvkk:
  sensitive_metrics: []
"""


@pytest.mark.asyncio
async def test_entity_type_not_in_pack_422(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    res = await async_client.post(
        "/v1/entities",
        json={"id": "no-pack-entity", "entity_type": "bilinmeyen_tip"},
        headers=headers,
    )
    assert res.status_code == 422
    err = res.json()
    assert err["error"]["code"] in ("unknown_entity_type", "no_active_pack_for_type")


@pytest.mark.asyncio
async def test_missing_required_fields_422(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": VALID_PACK_YAML, "pack_key": "val-pack"},
        headers=headers,
    )
    res = await async_client.post(
        "/v1/entities",
        json={"id": "missing-fields", "entity_type": "val_kurye", "fields": {"bolge": "Istanbul"}},
        headers=headers,
    )
    assert res.status_code == 422
    err = res.json()
    assert err["error"]["code"] == "missing_required_fields"


@pytest.mark.asyncio
async def test_inactive_pack_entity_create_403(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": VALID_PACK_YAML, "pack_key": "inactive-pack"},
        headers=headers,
    )
    from humetric.store import Store
    from humetric.db.database import get_async_session_factory
    from sqlalchemy import text
    factory = get_async_session_factory()
    async with factory() as session:
        await session.execute(text("SELECT set_config('app.tenant_id', '1', false)"))
        pack = await Store.get_pack(session, 1, "inactive-pack")
        pack.is_active = False
        await session.commit()

    res = await async_client.post(
        "/v1/entities",
        json={"id": "inactive-create", "entity_type": "val_kurye", "fields": {"bolge": "x", "arac_tipi": "x"}},
        headers=headers,
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "entity_type_locked"


@pytest.mark.asyncio
async def test_inactive_pack_entity_get_200(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    res = await async_client.get("/v1/entities/valid-entity", headers=headers)
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_inactive_pack_signal_403(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    res = await async_client.post(
        "/v1/signals",
        json={"entity_id": "valid-entity", "entity_type": "val_kurye", "text": "test signal"},
        headers=headers,
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "entity_type_locked"


@pytest.mark.asyncio
async def test_no_packs_tenant_create_entity_422(async_client):
    from humetric.store import Store
    from humetric.db.database import get_async_session_factory
    from sqlalchemy import text
    factory = get_async_session_factory()
    async with factory() as session:
        await session.execute(text("SELECT set_config('app.tenant_id', '2', false)"))
        tenant = await Store.get_tenant_by_kod(session, "empty")
        if not tenant:
            tenant = await Store.create_tenant(session, {"kod": "empty", "ad": "Empty"})
        full_key, _ = await Store.create_api_key(
            session,
            tenant_id=tenant.id,
            prefix="hm_test",
            label="Empty Key",
            scopes=["signals:write", "entities:read", "entities:write", "query", "packs:admin", "packs:read", "signals:read"],
        )

    headers = {"Authorization": f"Bearer {full_key}"}
    res = await async_client.post(
        "/v1/entities",
        json={"id": "no-pack-entity", "entity_type": "some_type"},
        headers=headers,
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "no_active_pack_for_type"


@pytest.mark.asyncio
async def test_update_entity_skips_required_fields_check(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    res = await async_client.post(
        "/v1/entities",
        json={
            "id": "update-skip",
            "entity_type": "val_kurye",
            "fields": {"bolge": "Istanbul", "arac_tipi": "motor"},
        },
        headers=headers,
    )
    assert res.status_code == 201

    res = await async_client.post(
        "/v1/entities",
        json={
            "id": "update-skip",
            "entity_type": "val_kurye",
            "fields": {"bolge": "Ankara"},
        },
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["fields"]["bolge"] == "Ankara"


@pytest.mark.asyncio
async def test_valid_entity_created_201(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": VALID_PACK_YAML, "pack_key": "val-pack-2"},
        headers=headers,
    )
    res = await async_client.post(
        "/v1/entities",
        json={
            "id": "valid-entity",
            "entity_type": "val_kurye",
            "fields": {"bolge": "Ankara", "arac_tipi": "motor"},
        },
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["entity_type"] == "val_kurye"
