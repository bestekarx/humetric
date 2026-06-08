"""Usage/metering service — signal/LLM/embedding counters, tier limit checks (Spec 026)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..config import (
    FREE_TIER_ENTITY_LIMIT,
    FREE_TIER_PACK_LIMIT,
    FREE_TIER_SIGNAL_LIMIT,
)
from ..db.database import get_sync_engine
from ..db.models import MeteringRecord, Tenant

logger = logging.getLogger("humetric.usage")

TIER_LIMITS = {
    "free": {
        "sinyal_sayisi": FREE_TIER_SIGNAL_LIMIT,
        "entity_count": FREE_TIER_ENTITY_LIMIT,
        "pack_count": FREE_TIER_PACK_LIMIT,
    },
    "pro": {
        "sinyal_sayisi": None,  # unlimited
        "entity_count": None,
        "pack_count": None,
    },
    "enterprise": {
        "sinyal_sayisi": None,
        "entity_count": None,
        "pack_count": None,
    },
}


def _upsert_usage(sync_engine, tenant_id: int, tarih: date, **fields) -> None:
    """Upsert the daily metering_record row (sync — called from the worker)."""
    with sync_engine.begin() as conn:
        stmt = pg_insert(MeteringRecord).values(
            tenant_id=tenant_id,
            tarih=tarih,
            **fields,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "tarih"],
            set_={k: MeteringRecord.__table__.c[k] + stmt.excluded[k] for k in fields},
        )
        conn.execute(stmt)


async def _upsert_usage_async(tenant_id: int, tarih: date, **fields) -> None:
    engine = get_sync_engine()
    await asyncio.to_thread(_upsert_usage, engine, tenant_id, tarih, **fields)


async def record_signal(tenant_id: int) -> None:
    await _upsert_usage_async(tenant_id, date.today(), sinyal_sayisi=1)


async def record_llm_tokens(tenant_id: int, count: int) -> None:
    await _upsert_usage_async(tenant_id, date.today(), llm_token_sayisi=count)


async def record_embedding(tenant_id: int) -> None:
    await _upsert_usage_async(tenant_id, date.today(), embedding_sayisi=1)


async def check_tier_limit(tenant_id: int, metric: str, current_value: int) -> bool:
    """Free tier limit check. True → limit not exceeded, False → limit exceeded."""
    def _check():
        engine = get_sync_engine()
        with engine.begin() as conn:
            tenant = conn.execute(
                select(Tenant.tier).where(Tenant.id == tenant_id)
            ).scalar_one_or_none()
        if not tenant or tenant not in TIER_LIMITS:
            return True
        limit = TIER_LIMITS[tenant].get(metric)
        if limit is None:
            return True  # paid tier — unlimited
        return current_value < limit

    return await asyncio.to_thread(_check)


async def get_current_usage(tenant_id: int) -> dict[str, int]:
    """Return the current month's usage totals."""
    engine = get_sync_engine()
    today = date.today()
    start_of_month = date(today.year, today.month, 1)

    def _query():
        with engine.begin() as conn:
            rows = conn.execute(
                select(MeteringRecord).where(
                    MeteringRecord.tenant_id == tenant_id,
                    MeteringRecord.tarih >= start_of_month,
                )
            ).all()
            return {
                "sinyal_sayisi": sum(r.sinyal_sayisi for r in rows),
                "entity_count": 0,  # comes from the tenant table
                "pack_count": 0,    # comes from the tenant table
            }

    return await asyncio.to_thread(_query)
