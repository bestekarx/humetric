"""Eval/replay: Idempotency, queue, and double-count prevention tests.

Verifies:
- Idempotency-Key prevents duplicate signal creation
- Signal does not double-count metrics if processed twice
- SKIP LOCKED prevents two workers from claiming the same task
- Retry does not cause double-count in EntityMetric
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://humetric:humetric@localhost:5434/humetric")
os.environ.setdefault("DATABASE_URL_APP", "postgresql+psycopg://humetric_app:humetric_app@localhost:5434/humetric")
os.environ.setdefault("HUMETRIC_AUTH_SECRET", "test-secret-for-pytest")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("VOYAGE_API_KEY", "test-key")

from humetric.store import Store
from humetric.db.models import Task, EntityMetric
from humetric.db.database import get_async_session_factory


@pytest.mark.asyncio
async def test_idempotency_key_prevents_duplicate_signal():
    """Same Idempotency-Key within 24h returns existing signal, no new task."""
    factory = get_async_session_factory()
    async with factory() as db:
        from sqlalchemy import text
        tenant = await Store.create_tenant(db, {"kod": "idm1", "ad": "Idempotency Test"})
        await db.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant.id)})

        entity = await Store.upsert_entity(db, {
            "id": "ent-idm-1",
            "tenant_id": tenant.id,
            "entity_type": "isci",
            "fields": {},
            "free_text": "Idempotency test",
        })

        signal = await Store.create_signal(db, {
            "id": "sig-idm-1",
            "tenant_id": tenant.id,
            "entity_id": entity.id,
            "entity_type": "isci",
            "text": "Test signal",
            "external_id": "idem-key-001",
            "status": "completed",
        })

        existing = await Store.check_idempotency(db, tenant.id, "idem-key-001", entity.id)
        assert existing is not None
        assert existing.id == signal.id


@pytest.mark.asyncio
async def test_idempotency_key_expires_after_24h():
    """Idempotency-Key older than 24h should not match."""
    factory = get_async_session_factory()
    async with factory() as db:
        from sqlalchemy import text
        tenant = await Store.create_tenant(db, {"kod": "idm2", "ad": "Idempotency Expiry"})
        await db.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant.id)})

        entity = await Store.upsert_entity(db, {
            "id": "ent-idm-2",
            "tenant_id": tenant.id,
            "entity_type": "isci",
            "fields": {},
            "free_text": "Old signal",
        })

        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        signal = await Store.create_signal(db, {
            "id": "sig-idm-2",
            "tenant_id": tenant.id,
            "entity_id": entity.id,
            "entity_type": "isci",
            "text": "Old test signal",
            "external_id": "old-key-002",
            "status": "completed",
        })
        from sqlalchemy import update
        from humetric.db.models import Signal
        await db.execute(
            update(Signal).where(Signal.id == signal.id).values(created_at=old_time)
        )
        await db.commit()

        existing = await Store.check_idempotency(db, tenant.id, "old-key-002", entity.id)
        assert existing is None


@pytest.mark.asyncio
async def test_get_next_task_skip_locked_excludes_processing():
    """SKIP LOCKED ensures processing tasks are not picked up again."""
    factory = get_async_session_factory()
    async with factory() as db:
        tenant = await Store.create_tenant(db, {"kod": "skl2", "ad": "SkipLock"})

        await Store.create_task(db, {
            "tenant_id": tenant.id,
            "task_type": "signal_process",
            "status": "queued",
            "payload": {"entity_id": "ent-1"},
        })
        await Store.create_task(db, {
            "tenant_id": tenant.id,
            "task_type": "signal_process",
            "status": "processing",
            "payload": {"entity_id": "ent-2"},
        })

        tasks = await Store.get_next_task(db, batch_size=5)
        assert len(tasks) == 1
        assert tasks[0].payload.get("entity_id") == "ent-1"


@pytest.mark.asyncio
async def test_upsert_metric_is_idempotent():
    """Upserting the same metric twice does not create duplicate rows."""
    factory = get_async_session_factory()
    async with factory() as db:
        tenant = await Store.create_tenant(db, {"kod": "ups1", "ad": "Upsert Test"})

        data = {
            "tenant_id": tenant.id,
            "entity_id": "ent-ups-1",
            "metric_key": "dakiklik",
            "value": 0.75,
            "confidence": 0.8,
            "source_count": 1,
            "signal_id": "sig-ups-1",
        }

        m1 = await Store.upsert_metric(db, dict(data))
        m2 = await Store.upsert_metric(db, dict(data))

        assert m1.id == m2.id

        from sqlalchemy import select, func
        count = await db.scalar(
            select(func.count()).select_from(EntityMetric).where(
                EntityMetric.entity_id == "ent-ups-1",
                EntityMetric.metric_key == "dakiklik",
            )
        )
        assert count == 1


@pytest.mark.asyncio
async def test_task_retry_does_not_double_count():
    """Task retry should not cause metric double-count since upsert is idempotent."""
    factory = get_async_session_factory()
    async with factory() as db:
        tenant = await Store.create_tenant(db, {"kod": "rtc1", "ad": "Retry Count"})

        data = {
            "tenant_id": tenant.id,
            "entity_id": "ent-rtc-1",
            "metric_key": "titizlik",
            "value": 0.6,
            "confidence": 0.7,
            "source_count": 1,
            "signal_id": "sig-rtc-1",
        }

        await Store.upsert_metric(db, dict(data))
        await Store.upsert_metric(db, {
            **data,
            "value": 0.65,
            "signal_id": "sig-rtc-1",
        })

        from sqlalchemy import select, func
        count = await db.scalar(
            select(func.count()).select_from(EntityMetric).where(
                EntityMetric.entity_id == "ent-rtc-1",
                EntityMetric.metric_key == "titizlik",
            )
        )
        assert count == 1

        from sqlalchemy import select as sel
        result = await db.execute(
            sel(EntityMetric).where(
                EntityMetric.entity_id == "ent-rtc-1",
                EntityMetric.metric_key == "titizlik",
            )
        )
        metric = result.scalar_one()
        assert metric.value == 0.65
        assert metric.signal_id == "sig-rtc-1"


@pytest.mark.asyncio
async def test_signal_input_hash_is_set():
    """Signal input_hash should be computed and stored during processing."""
    factory = get_async_session_factory()
    async with factory() as db:
        from humetric.agents.versioning import hash_text
        from sqlalchemy import text
        tenant = await Store.create_tenant(db, {"kod": "ish1", "ad": "InputHash"})
        await db.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant.id)})

        entity = await Store.upsert_entity(db, {
            "id": "ent-ish-1",
            "tenant_id": tenant.id,
            "entity_type": "isci",
            "fields": {},
            "free_text": "Hash test",
        })

        signal_text = "Test signal for hashing"
        expected_hash = hash_text(signal_text)

        signal = await Store.create_signal(db, {
            "id": "sig-ish-1",
            "tenant_id": tenant.id,
            "entity_id": entity.id,
            "entity_type": "isci",
            "text": signal_text,
            "input_hash": expected_hash,
        })

        assert signal.input_hash == expected_hash
        assert len(signal.input_hash) == 64
