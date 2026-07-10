"""Pack CRUD endpoint testleri (Spec 023)."""

import pytest


VALID_PACK_YAML = """entity_type: test_kurye
label: "Test Kurye Performansi"
version: 1
required_fields:
  - bolge
metrics:
  - key: hiz
    label: "Hiz"
    type: float
    prompt: "Ne kadar hizli?"
    default_confidence: 0.7
    sensitive: false
prompts:
  extraction: "Metrikleri cikar."
  curation: "Dogrula."
kvkk:
  sensitive_metrics: []
"""


@pytest.mark.asyncio
async def test_create_pack_201(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    res = await async_client.post(
        "/v1/packs",
        json={"yaml_text": VALID_PACK_YAML, "pack_key": "test-kurye"},
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["pack_key"] == "test-kurye"
    assert data["entity_type"] == "test_kurye"
    assert data["version"] == 1
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_duplicate_pack_key_409(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": VALID_PACK_YAML, "pack_key": "dup-key"},
        headers=headers,
    )
    res = await async_client.post(
        "/v1/packs",
        json={"yaml_text": VALID_PACK_YAML, "pack_key": "dup-key"},
        headers=headers,
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_invalid_yaml_422(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    res = await async_client.post(
        "/v1/packs",
        json={"yaml_text": "::: invalid yaml :::", "pack_key": "bad-yaml"},
        headers=headers,
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_get_pack_200(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": VALID_PACK_YAML, "pack_key": "get-test"},
        headers=headers,
    )
    res = await async_client.get("/v1/packs/get-test", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["pack_key"] == "get-test"
    assert "definition" in data


@pytest.mark.asyncio
async def test_get_pack_404(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    res = await async_client.get("/v1/packs/nonexistent", headers=headers)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_list_packs(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": VALID_PACK_YAML, "pack_key": "list-test"},
        headers=headers,
    )
    res = await async_client.get("/v1/packs", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert any(p["pack_key"] == "list-test" for p in data)


@pytest.mark.asyncio
async def test_update_pack_version_bump(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": VALID_PACK_YAML, "pack_key": "update-test"},
        headers=headers,
    )
    updated = VALID_PACK_YAML.replace("version: 1", "version: 2")
    res = await async_client.put(
        "/v1/packs/update-test",
        json={"yaml_text": updated, "pack_key": "update-test"},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["version"] == 2


@pytest.mark.asyncio
async def test_duplicate_entity_type_409(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    pack1 = VALID_PACK_YAML.replace("test_kurye", "shared_type")
    pack2 = VALID_PACK_YAML.replace("test_kurye", "shared_type")
    await async_client.post(
        "/v1/packs",
        json={"yaml_text": pack1, "pack_key": "pack-a"},
        headers=headers,
    )
    res = await async_client.post(
        "/v1/packs",
        json={"yaml_text": pack2, "pack_key": "pack-b"},
        headers=headers,
    )
    assert res.status_code == 409
    err = res.json()
    assert err["error"]["code"] == "entity_type_already_active"


@pytest.mark.asyncio
async def test_missing_required_section_422(async_client, test_api_key):
    headers = {"Authorization": f"Bearer {test_api_key}"}
    invalid = """entity_type: test
label: Test
version: 1
"""
    res = await async_client.post(
        "/v1/packs",
        json={"yaml_text": invalid, "pack_key": "bad-section"},
        headers=headers,
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_create_pack_unauthorized(async_client, test_api_key):
    """packs:read scope ile POST yapilamaz."""
    from humetric.store import Store
    from humetric.db.database import get_async_session_factory
    from sqlalchemy import text

    factory = get_async_session_factory()
    async with factory() as session:
        await session.execute(text("SELECT set_config('app.tenant_id', '1', false)"))
        full_key, _ = await Store.create_api_key(
            session,
            tenant_id=1,
            prefix="hm_test",
            label="Readonly Key",
            scopes=["packs:read"],
        )

    headers = {"Authorization": f"Bearer {full_key}"}
    res = await async_client.post(
        "/v1/packs",
        json={"yaml_text": VALID_PACK_YAML, "pack_key": "no-perm"},
        headers=headers,
    )
    assert res.status_code == 403
