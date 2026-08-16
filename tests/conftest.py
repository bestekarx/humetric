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

# AYRI test veritabani — gelistirme DB'si DEGIL.
#
# Bu dosya eskiden dogrudan `humetric` veritabanina baglaniyordu: testler
# gercek tenant'larin yanina sabit kodlu ("test") tenant'lar ve sabit id'li
# ("entity-1") entity'ler yaziyor, hicbirini temizlemiyordu. Sonuc iki yonlu
# zarardi — testler ikinci kosuda "duplicate key" ile patliyor, gelistirme
# verisi de test cop'uyle doluyordu.
#
# Kurulum (bir kez):
#   docker exec humetric-db-1 psql -U humetric -d postgres -c "CREATE DATABASE humetric_test OWNER humetric;"
#   docker exec humetric-db-1 psql -U humetric -d humetric_test -c "CREATE EXTENSION IF NOT EXISTS vector;"
#   DATABASE_URL=...humetric_test DATABASE_URL_APP=...humetric_test alembic upgrade head
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://humetric:humetric@localhost:5434/humetric_test")
os.environ.setdefault("DATABASE_URL_APP", "postgresql+psycopg://humetric_app:humetric_app@localhost:5434/humetric_test")
os.environ.setdefault("HUMETRIC_AUTH_SECRET", "test-secret-for-pytest")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("VOYAGE_API_KEY", "test-key")

from humetric import config
from humetric.api import app
from humetric.db.database import (
    get_async_session_factory,
    get_admin_async_session_factory,
    get_async_engine,
    get_sync_engine,
    Base,
)
from humetric import auth, config  # noqa: F401
from humetric.db import models  # noqa: F401

_FIXTURE_TENANT_CODE = "pytest-fixture"


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _clean_database():
    """Empty the test DB at the start of each run.

    Tests use fixed identifiers (code="test", id="entity-1"), so rows left
    over from a previous run would knock out the second run with a
    "duplicate key" error — worse, the failing INSERT would abort the
    session and take down the following tests that share that SAME session.

    Safety: only runs against a database whose name ends in `_test`. If
    accidentally pointed at a dev database, it deletes nothing — the tests
    just fail on dirty data instead, which is better than silently losing data.
    """
    from sqlalchemy import text

    factory = get_admin_async_session_factory()
    async with factory() as session:
        db_name = (await session.execute(text("SELECT current_database()"))).scalar_one()
        if not str(db_name).endswith("_test"):
            raise RuntimeError(
                f"Testler '{db_name}' veritabanina yoneltilmis. Ayri bir test DB'si "
                "bekleniyor (adi '_test' ile bitmeli); gelistirme verisini silmemek "
                "icin temizlik atlandi."
            )
        # TRUNCATE ... CASCADE tek islemde tum FK zincirini bosaltir; RESTART
        # IDENTITY sayaci sifirlar ki id bekleyen testler kosudan kosuya kaymasin.
        tables = (await session.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"
        ))).scalars().all()
        if tables:
            joined = ", ".join(f'"{t}"' for t in tables)
            await session.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))
            await session.commit()
    yield


@pytest_asyncio.fixture(scope="session")
async def tenant_id(_clean_database) -> int:
    """Test tenant'ini superuser baglantisiyla hazirla, GERCEK id'sini dondur.

    RLS politikasi her tabloda `tenant_id = current_setting('app.tenant_id')`
    istiyor. Onceki hal GUC'a sabit '1' yaziyordu, oysa test tenant'inin id'si
    baska (ornekte 4) — bu yuzden RLS'li her tabloya insert
    "new row violates row-level security policy" ile reddediliyor, DB'ye dokunan
    her test hata veriyordu. Tenant'i once olusturup id'yi GUC'a yazmak sart.

    Tenant satirinin kendisi de RLS altinda oldugundan olusturma islemi
    kisitli `humetric_app` roluyle yapilamaz; admin session'i o yuzden burada.
    """
    from sqlalchemy import select
    from humetric.db.models import Tenant

    factory = get_admin_async_session_factory()
    async with factory() as session:
        # Kod "test" DEGIL: bircok test kendi icinde code="test" tenant'i
        # olusturuyor, ayni adi kullanmak "duplicate key" ile carpisiyordu.
        result = await session.execute(select(Tenant).where(Tenant.code == _FIXTURE_TENANT_CODE))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(code=_FIXTURE_TENANT_CODE, name="Pytest Fixture Tenant")
            session.add(tenant)
            await session.commit()
            await session.refresh(tenant)
        return tenant.id


@pytest_asyncio.fixture
async def test_db(tenant_id):
    """Async session — tenant baglamli. Test sonunda rollback."""
    factory = get_async_session_factory()
    async with factory() as session:
        from sqlalchemy import text
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, false)").bindparams(tid=str(tenant_id))
        )
        yield session


@pytest_asyncio.fixture
async def test_tenant(test_db, tenant_id):
    from sqlalchemy import select
    from humetric.db.models import Tenant

    result = await test_db.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one()


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
