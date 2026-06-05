"""Worker retry ve graceful shutdown testleri (Spec 024, US3).

On kosul: docker compose up -d calisiyor olmali.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://humetric:humetric@localhost:5434/humetric")
os.environ.setdefault("DATABASE_URL_APP", "postgresql+psycopg://humetric_app:humetric_app@localhost:5434/humetric")
os.environ.setdefault("HUMETRIC_AUTH_SECRET", "test-secret-for-pytest")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("VOYAGE_API_KEY", "test-key")

from humetric.store import Store
from humetric.db.models import Task
from humetric.db.database import get_async_session_factory


@pytest.mark.asyncio
async def test_retry_schedule_increments_retry_count(client):
    """US3: Retry schedule retry_count artirir ve next_retry_at set eder."""
    factory = get_async_session_factory()
    async with factory() as db:
        from humetric import auth as humetric_auth
        tenant = await Store.create_tenant(db, {"kod": "rty", "ad": "Retry Test"})
        signal = None
        task = await Store.create_task(db, {
            "tenant_id": tenant.id,
            "signal_id": None,
            "task_type": "signal_process",
            "status": "processing",
            "payload": {"entity_id": "ent-1"},
            "retry_count": 1,
            "max_retries": 3,
        })

        next_retry = datetime.now(timezone.utc) + timedelta(seconds=4)
        await Store.schedule_retry(db, task.id, next_retry)

        from sqlalchemy import select
        result = await db.execute(select(Task).where(Task.id == task.id))
        updated = result.scalar_one()
        assert updated.retry_count == 2
        assert updated.status == "queued"
        assert updated.next_retry_at is not None


@pytest.mark.asyncio
async def test_fail_task_permanently(client):
    """US3: max_retries asildiginda task failed olur."""
    factory = get_async_session_factory()
    async with factory() as db:
        from humetric import auth as humetric_auth
        tenant = await Store.create_tenant(db, {"kod": "flt", "ad": "Fail Test"})
        task = await Store.create_task(db, {
            "tenant_id": tenant.id,
            "signal_id": None,
            "task_type": "signal_process",
            "status": "processing",
            "payload": {"entity_id": "ent-2"},
            "retry_count": 3,
            "max_retries": 3,
        })

        await Store.fail_task_permanently(db, task.id, "Max retries exceeded")

        from sqlalchemy import select
        result = await db.execute(select(Task).where(Task.id == task.id))
        updated = result.scalar_one()
        assert updated.status == "failed"
        assert updated.last_error == "Max retries exceeded"


@pytest.mark.asyncio
async def test_get_next_task_skip_locked(client):
    """US1: Store.get_next_task SELECT FOR UPDATE SKIP LOCKED ile task ceker."""
    factory = get_async_session_factory()
    async with factory() as db:
        from humetric import auth as humetric_auth
        tenant = await Store.create_tenant(db, {"kod": "skl", "ad": "SkipLock Test"})
        for i in range(3):
            await Store.create_task(db, {
                "tenant_id": tenant.id,
                "signal_id": None,
                "task_type": "signal_process",
                "status": "queued",
                "payload": {"entity_id": f"ent-{i}"},
            })

        tasks = await Store.get_next_task(db, batch_size=5)
        assert len(tasks) == 3
        for t in tasks:
            assert t.status == "processing"
            assert t.started_at is not None


@pytest.mark.asyncio
async def test_healthz_worker_reflects_queue_depth(client):
    """US1: healthz/worker kuyruktaki task sayisini yansitir."""
    factory = get_async_session_factory()
    async with factory() as db:
        from humetric import auth as humetric_auth
        tenant = await Store.create_tenant(db, {"kod": "hqw", "ad": "HealthQ Test"})
        await Store.create_task(db, {
            "tenant_id": tenant.id,
            "signal_id": None,
            "task_type": "signal_process",
            "status": "queued",
            "payload": {"entity_id": "ent-h"},
        })

    from humetric.api import app as test_app
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/healthz/worker")
        assert resp.status_code == 200
        data = resp.json()
        assert data["queue_depth"] >= 1
        assert data["workers"] == 1
