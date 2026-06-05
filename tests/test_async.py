"""Async sinyal isleme ve idempotency testleri (Spec 024, US1 + US2).

On kosul: docker compose up -d calisiyor olmali.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://humetric:humetric@localhost:5434/humetric")
os.environ.setdefault("DATABASE_URL_APP", "postgresql+psycopg://humetric_app:humetric_app@localhost:5434/humetric")
os.environ.setdefault("HUMETRIC_AUTH_SECRET", "test-secret-for-pytest")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("VOYAGE_API_KEY", "test-key")

from humetric.api import app
from humetric.db.models import Signal, Task
from humetric.store import Store
from humetric.db.database import get_async_session_factory


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _seed_tenant_and_key():
    factory = get_async_session_factory()
    async with factory() as db:
        from humetric import auth as humetric_auth
        tenant = await Store.create_tenant(db, {"kod": "test", "ad": "Test Tenant"})
        full_key, api_key = await Store.create_api_key(
            db, tenant_id=tenant.id, prefix="hm_live",
            label="test", scopes=["signals:write", "signals:read", "entities:read", "entities:write"],
        )
        def_str = ("metrics:\n  - key: puan\n    label: Puan\n    type: float\n    "
                    "sensitive: false\n    prompt: test\n    default_confidence: 0.5\n")
        await Store.create_pack(db, tenant.id, "test_pack", 1, {
            "entity_type": "driver",
            "label": "Test Pack",
            "version": 1,
            "required_fields": [],
            "metrics": [{"key": "puan", "label": "Puan", "type": "float", "sensitive": False, "prompt": "test", "default_confidence": 0.5}],
            "prompts": {"extraction": "", "curation": ""},
            "kvkk": {"sensitive_metrics": []},
        })
        return tenant.id, full_key


@pytest.mark.asyncio
async def test_signal_returns_202_and_received_status(client):
    """US1: POST /v1/signals → 202, status=received."""
    tenant_id, api_key = await _seed_tenant_and_key()

    await client.post("/v1/entities", json={
        "id": "driver-1", "entity_type": "driver",
    }, headers={"Authorization": f"Bearer {api_key}"})

    resp = await client.post("/v1/signals", json={
        "entity_id": "driver-1", "entity_type": "driver", "text": "Test signal",
    }, headers={"Authorization": f"Bearer {api_key}"})

    assert resp.status_code == 202
    data = resp.json()
    assert "signal_id" in data
    assert data["status"] == "received"
    assert "trace_url" in data


@pytest.mark.asyncio
async def test_signal_creates_task_in_queue(client):
    """US1: Sinyal gonderildiginde task tablosunda queued task olusur."""
    tenant_id, api_key = await _seed_tenant_and_key()

    await client.post("/v1/entities", json={
        "id": "driver-2", "entity_type": "driver",
    }, headers={"Authorization": f"Bearer {api_key}"})

    resp = await client.post("/v1/signals", json={
        "entity_id": "driver-2", "entity_type": "driver", "text": "Great performance +0.8",
    }, headers={"Authorization": f"Bearer {api_key}"})

    assert resp.status_code == 202

    factory = get_async_session_factory()
    async with factory() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(Task).where(Task.signal_id == resp.json()["signal_id"])
        )
        task = result.scalar_one_or_none()
        assert task is not None
        assert task.status == "queued"
        assert task.task_type == "signal_process"


@pytest.mark.asyncio
async def test_get_signal_shows_status(client):
    """US1: GET /v1/signals/{id} sinyal durumunu gosterir."""
    tenant_id, api_key = await _seed_tenant_and_key()

    await client.post("/v1/entities", json={
        "id": "driver-3", "entity_type": "driver",
    }, headers={"Authorization": f"Bearer {api_key}"})

    resp = await client.post("/v1/signals", json={
        "entity_id": "driver-3", "entity_type": "driver", "text": "Signal",
    }, headers={"Authorization": f"Bearer {api_key}"})
    signal_id = resp.json()["signal_id"]

    get_resp = await client.get(f"/v1/signals/{signal_id}", headers={"Authorization": f"Bearer {api_key}"})
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["signal_id"] == signal_id
    assert data["status"] in ("received", "processing", "completed")


@pytest.mark.asyncio
async def test_idempotency_key_duplicate_returns_200(client):
    """US2: Ayni Idempotency-Key ile ikinci istek 200 doner, ayni signal_id."""
    tenant_id, api_key = await _seed_tenant_and_key()

    await client.post("/v1/entities", json={
        "id": "driver-4", "entity_type": "driver",
    }, headers={"Authorization": f"Bearer {api_key}"})

    key = "idem-test-001"
    resp1 = await client.post("/v1/signals", json={
        "entity_id": "driver-4", "entity_type": "driver", "text": "Signal 1",
    }, headers={"Authorization": f"Bearer {api_key}", "Idempotency-Key": key})

    assert resp1.status_code == 202
    signal_id_1 = resp1.json()["signal_id"]

    resp2 = await client.post("/v1/signals", json={
        "entity_id": "driver-4", "entity_type": "driver", "text": "Signal 2",
    }, headers={"Authorization": f"Bearer {api_key}", "Idempotency-Key": key})

    assert resp2.status_code == 200
    assert resp2.json()["signal_id"] == signal_id_1


@pytest.mark.asyncio
async def test_idempotency_key_expired_creates_new_signal(client):
    """US2: 24 saatten eski Idempotency-Key yeni signal uretir."""
    tenant_id, api_key = await _seed_tenant_and_key()

    await client.post("/v1/entities", json={
        "id": "driver-5", "entity_type": "driver",
    }, headers={"Authorization": f"Bearer {api_key}"})

    key = "idem-test-002"
    resp1 = await client.post("/v1/signals", json={
        "entity_id": "driver-5", "entity_type": "driver", "text": "Old signal",
    }, headers={"Authorization": f"Bearer {api_key}", "Idempotency-Key": key})

    signal_id_1 = resp1.json()["signal_id"]

    factory = get_async_session_factory()
    async with factory() as db:
        from sqlalchemy import select
        result = await db.execute(select(Signal).where(Signal.id == signal_id_1))
        signal = result.scalar_one()
        signal.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        await db.commit()

    resp3 = await client.post("/v1/signals", json={
        "entity_id": "driver-5", "entity_type": "driver", "text": "New signal",
    }, headers={"Authorization": f"Bearer {api_key}", "Idempotency-Key": key})

    assert resp3.status_code == 202
    assert resp3.json()["signal_id"] != signal_id_1


@pytest.mark.asyncio
async def test_different_tenant_same_key_isolated(client):
    """US2: Farkli tenant'ta ayni Idempotency-Key bagimsizdir."""
    tenant_id, api_key = await _seed_tenant_and_key()

    factory = get_async_session_factory()
    async with factory() as db:
        tenant2 = await Store.create_tenant(db, {"kod": "test2async", "ad": "Test2"})
        from humetric import auth as humetric_auth
        full_key2, api_key2 = await Store.create_api_key(
            db, tenant_id=tenant2.id, prefix="hm_live",
            label="test2", scopes=["signals:write", "entities:read", "entities:write"],
        )
        def_str = ("metrics:\n  - key: puan\n    label: Puan\n    type: float\n    "
                    "sensitive: false\n    prompt: test\n    default_confidence: 0.5\n")
        await Store.create_pack(db, tenant2.id, "test_pack", 1, {
            "entity_type": "driver",
            "label": "Test Pack",
            "version": 1,
            "required_fields": [],
            "metrics": [{"key": "puan", "label": "Puan", "type": "float", "sensitive": False, "prompt": "test", "default_confidence": 0.5}],
            "prompts": {"extraction": "", "curation": ""},
            "kvkk": {"sensitive_metrics": []},
        })

    await client.post("/v1/entities", json={
        "id": "driver-6", "entity_type": "driver",
    }, headers={"Authorization": f"Bearer {api_key}"})

    await client.post("/v1/entities", json={
        "id": "driver-t2", "entity_type": "driver",
    }, headers={"Authorization": f"Bearer {full_key2}"})

    key = "cross-tenant-key"
    resp1 = await client.post("/v1/signals", json={
        "entity_id": "driver-6", "entity_type": "driver", "text": "T1",
    }, headers={"Authorization": f"Bearer {api_key}", "Idempotency-Key": key})
    assert resp1.status_code == 202

    resp2 = await client.post("/v1/signals", json={
        "entity_id": "driver-t2", "entity_type": "driver", "text": "T2",
    }, headers={"Authorization": f"Bearer {full_key2}", "Idempotency-Key": key})
    assert resp2.status_code == 202
    assert resp2.json()["signal_id"] != resp1.json()["signal_id"]


@pytest.mark.asyncio
async def test_no_idempotency_key_creates_new_each_time(client):
    """US2: Idempotency-Key olmadan her istek yeni signal uretir."""
    tenant_id, api_key = await _seed_tenant_and_key()

    await client.post("/v1/entities", json={
        "id": "driver-7", "entity_type": "driver",
    }, headers={"Authorization": f"Bearer {api_key}"})

    resp1 = await client.post("/v1/signals", json={
        "entity_id": "driver-7", "entity_type": "driver", "text": "A",
    }, headers={"Authorization": f"Bearer {api_key}"})
    resp2 = await client.post("/v1/signals", json={
        "entity_id": "driver-7", "entity_type": "driver", "text": "B",
    }, headers={"Authorization": f"Bearer {api_key}"})

    assert resp1.status_code == 202
    assert resp2.status_code == 202
    assert resp1.json()["signal_id"] != resp2.json()["signal_id"]


@pytest.mark.asyncio
async def test_healthz_worker_endpoint(client):
    """US1: GET /healthz/worker dogru shaped response doner."""
    resp = await client.get("/healthz/worker")
    assert resp.status_code == 200
    data = resp.json()
    assert "workers" in data
    assert "queue_depth" in data
    assert "oldest_pending_seconds" in data
    assert "failed_last_hour" in data
    assert isinstance(data["queue_depth"], int)
