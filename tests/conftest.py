"""Pytest fixtures — async client, test DB, test tenant, test API key.

Docker Postgres kullanir (RLS pgvector gerektirir).
On kosul: docker compose up -d calisiyor olmali.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://humetric:humetric@localhost:5434/humetric")
os.environ.setdefault("DATABASE_URL_APP", "postgresql+psycopg://humetric_app:humetric_app@localhost:5434/humetric")
os.environ.setdefault("HUMETRIC_AUTH_SECRET", "test-secret-for-pytest")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("VOYAGE_API_KEY", "test-key")

from humetric import config
from humetric.api import app
from humetric.db.database import (
    get_async_session_factory,
    get_async_engine,
    get_sync_engine,
    Base,
)
from humetric import auth, config  # noqa: F401
from humetric.db import models  # noqa: F401


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def test_db():
    """Async session — tenant baglamli. Test sonunda rollback."""
    factory = get_async_session_factory()
    async with factory() as session:
        from sqlalchemy import text
        await session.execute(
            text("SELECT set_config('app.tenant_id', '1', false)")
        )
        yield session


@pytest_asyncio.fixture
async def test_tenant(test_db):
    from humetric.store import Store
    from sqlalchemy import select
    from humetric.db.models import Tenant

    result = await test_db.execute(select(Tenant).where(Tenant.kod == "test"))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = await Store.create_tenant(test_db, {
            "kod": "test",
            "ad": "Test Tenant",
        })
    return tenant


@pytest_asyncio.fixture
async def test_api_key(test_db, test_tenant):
    from humetric.store import Store
    full_key, _ = await Store.create_api_key(
        test_db,
        tenant_id=test_tenant.id,
        prefix="hm_test",
        label="Test Key",
        scopes=["signals:write", "entities:read", "entities:write", "query", "packs:admin", "packs:read", "signals:read"],
    )
    return full_key
