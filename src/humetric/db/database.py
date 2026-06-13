"""SQLAlchemy engine and session management — async (runtime) + sync (alembic).

PostgreSQL 15 + pgvector. RLS isolation: get_tenant_db() applies
set_config('app.tenant_id', ...) at session start; the RLS policy is fail-closed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

from pgvector.sqlalchemy import Vector  # noqa: F401
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .. import config

Base = declarative_base()

_sync_engine = None
_SessionLocal = None

_async_engine = None
_AsyncSessionLocal = None

_admin_async_engine = None
_AdminAsyncSessionLocal = None


def _get_sync_url() -> str:
    return config.DATABASE_URL.replace("+asyncpg", "+psycopg") if "+asyncpg" in config.DATABASE_URL else config.DATABASE_URL


def get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        config.require_db()
        url = _get_sync_url()
        _sync_engine = create_engine(url, pool_pre_ping=True, echo=False)

        @event.listens_for(_sync_engine, "connect")
        def _register_vector(dbapi_conn, _):
            from pgvector.psycopg import register_vector
            register_vector(dbapi_conn)
    return _sync_engine


def get_sync_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_sync_engine(), autocommit=False, autoflush=False)
    return _SessionLocal


def get_sync_db() -> Generator[Session, None, None]:
    """Sync session — for Alembic migrations and seeding."""
    SessionLocal = get_sync_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_async_engine():
    global _async_engine
    if _async_engine is None:
        config.require_db()
        url = config.DATABASE_URL_APP
        if "+asyncpg" not in url:
            url = url.replace("+psycopg", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")
        _async_engine = create_async_engine(url, pool_pre_ping=True, echo=False)
    return _async_engine


def get_async_session_factory():
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _AsyncSessionLocal = async_sessionmaker(
            bind=get_async_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _AsyncSessionLocal


def get_admin_async_session_factory():
    """Superuser async session — uses DATABASE_URL (admin role, RLS bypass).

    The engine is cached globally so connection pools are not leaked on every call.
    """
    global _admin_async_engine, _AdminAsyncSessionLocal
    if _AdminAsyncSessionLocal is None:
        config.require_db()
        url = config.DATABASE_URL
        if "+asyncpg" not in url:
            url = url.replace("+psycopg", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")
        _admin_async_engine = create_async_engine(url, pool_pre_ping=True, echo=False)
        _AdminAsyncSessionLocal = async_sessionmaker(
            bind=_admin_async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _AdminAsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends: request-scoped async session (NO tenant context)."""
    factory = get_async_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_tenant_db(api_key_id: int, tenant_id: int) -> AsyncGenerator[AsyncSession, None]:
    """Async session with tenant context applied.

    The tenant_id is known once the API key is resolved. This session sets
    the PostgreSQL GUC `app.tenant_id`; RLS policies read it. The GUC is
    reset when the session closes (no connection-pool leakage).

    set_config() is called with a parametrized query — safe from SQL injection.
    """
    factory = get_async_session_factory()
    async with factory() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, false)"),
                {"t": str(tenant_id)},
            )
            yield session
        finally:
            try:
                await session.execute(
                    text("SELECT set_config('app.tenant_id', '', false)")
                )
            except Exception:
                pass
            await session.close()
