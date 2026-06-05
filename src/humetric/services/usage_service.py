"""Usage/metering servisi — sinyal/LLM/embedding sayaclari, tier limit kontrolu (Spec 026)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select, update
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
        "sinyal_sayisi": None,  # limitsiz
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
    """Upsert gunluk metering_record satiri (sync — worker'dan cagrilir)."""
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


async def record_signal(tenant_id: int) -> None:
    engine = get_sync_engine()
    _upsert_usage(engine, tenant_id, date.today(), sinyal_sayisi=1)


async def record_llm_tokens(tenant_id: int, count: int) -> None:
    engine = get_sync_engine()
    _upsert_usage(engine, tenant_id, date.today(), llm_token_sayisi=count)


async def record_embedding(tenant_id: int) -> None:
    engine = get_sync_engine()
    _upsert_usage(engine, tenant_id, date.today(), embedding_sayisi=1)


async def check_tier_limit(tenant_id: int, metric: str, current_value: int) -> bool:
    """Free tier limit kontrolu. True → limit asilmadi, False → limit asildi."""
    engine = get_sync_engine()
    with engine.begin() as conn:
        tenant = conn.execute(
            select(Tenant.tier).where(Tenant.id == tenant_id)
        ).scalar_one_or_none()
    if not tenant or tenant not in TIER_LIMITS:
        return True
    limit = TIER_LIMITS[tenant].get(metric)
    if limit is None:
        return True  # paid tier — limitsiz
    return current_value < limit
