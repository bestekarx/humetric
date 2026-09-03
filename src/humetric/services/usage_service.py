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
from ..db.models import LlmCallRecord, MeteringRecord, Tenant

logger = logging.getLogger("humetric.usage")

TIER_LIMITS = {
    "free": {
        "signal_count": FREE_TIER_SIGNAL_LIMIT,
        "entity_count": FREE_TIER_ENTITY_LIMIT,
        "pack_count": FREE_TIER_PACK_LIMIT,
    },
    "pro": {
        "signal_count": None,  # unlimited
        "entity_count": None,
        "pack_count": None,
    },
    "enterprise": {
        "signal_count": None,
        "entity_count": None,
        "pack_count": None,
    },
}


def _upsert_usage(sync_engine, tenant_id: int, date_val: date, **fields) -> None:
    """Upsert the daily metering_record row (sync — called from the worker)."""
    with sync_engine.begin() as conn:
        stmt = pg_insert(MeteringRecord).values(
            tenant_id=tenant_id,
            date=date_val,
            **fields,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "date"],
            set_={k: MeteringRecord.__table__.c[k] + stmt.excluded[k] for k in fields},
        )
        conn.execute(stmt)


async def _upsert_usage_async(tenant_id: int, date_val: date, **fields) -> None:
    engine = get_sync_engine()
    await asyncio.to_thread(_upsert_usage, engine, tenant_id, date_val, **fields)


async def record_signal(tenant_id: int) -> None:
    await _upsert_usage_async(tenant_id, date.today(), signal_count=1)


def _insert_llm_call_record(
    sync_engine,
    tenant_id: int,
    *,
    signal_id: str | None,
    pack_key: str | None,
    pack_version: int | None,
    provider: str | None,
    model: str | None,
    token_count: int,
) -> None:
    with sync_engine.begin() as conn:
        conn.execute(
            LlmCallRecord.__table__.insert().values(
                tenant_id=tenant_id,
                signal_id=signal_id,
                pack_key=pack_key,
                pack_version=pack_version,
                provider=provider,
                model=model,
                token_count=token_count,
            )
        )


async def record_llm_tokens(
    tenant_id: int,
    count: int,
    *,
    signal_id: str | None = None,
    pack_key: str | None = None,
    pack_version: int | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Record LLM token usage: always the daily tenant total, plus a granular
    llm_call_record row whenever the call is attributable to a signal or pack
    (i.e. a real pipeline call, not some other token-spending path)."""
    await _upsert_usage_async(tenant_id, date.today(), llm_token_count=count)
    if signal_id or pack_key:
        engine = get_sync_engine()
        await asyncio.to_thread(
            _insert_llm_call_record,
            engine,
            tenant_id,
            signal_id=signal_id,
            pack_key=pack_key,
            pack_version=pack_version,
            provider=provider,
            model=model,
            token_count=count,
        )


async def record_embedding(tenant_id: int) -> None:
    await _upsert_usage_async(tenant_id, date.today(), embedding_count=1)


async def check_tier_limit(tenant_id: int, metric: str, current_value: int) -> bool:
    """Free tier limit check. True → limit not exceeded, False → limit exceeded."""
    from .trial_service import effective_tier

    def _check():
        engine = get_sync_engine()
        with engine.begin() as conn:
            row = conn.execute(
                select(Tenant.tier, Tenant.trial_status, Tenant.trial_ends_at)
                .where(Tenant.id == tenant_id)
            ).one_or_none()
        if not row:
            return True
        # An expired-but-not-yet-swept trial must not keep granting Pro quota.
        tenant = effective_tier(row.tier, row.trial_status, row.trial_ends_at)
        if tenant not in TIER_LIMITS:
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
                    MeteringRecord.date >= start_of_month,
                )
            ).all()
            return {
                "signal_count": sum(r.signal_count for r in rows),
                "entity_count": 0,  # comes from the tenant table
                "pack_count": 0,    # comes from the tenant table
            }

    return await asyncio.to_thread(_query)
