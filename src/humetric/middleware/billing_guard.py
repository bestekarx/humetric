"""Tier limit guard middleware — enforces free-tier signal/entity/pack limits (Spec 026).

When ENFORCE_TIER_LIMITS=true, checks POST operations for free-tier tenants
and returns 402 tier_limit_exceeded once the limit is exceeded.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..config import (
    ENFORCE_TIER_LIMITS,
    FREE_TIER_ENTITY_LIMIT,
    FREE_TIER_PACK_LIMIT,
    FREE_TIER_SIGNAL_LIMIT,
)
from ..schema import TierLimitExceededResponse

_log = logging.getLogger(__name__)

RESOURCE_LIMITS = {
    "/v1/signals": ("sinyal_sayisi", FREE_TIER_SIGNAL_LIMIT),
    "/v1/entities": ("entity_count", FREE_TIER_ENTITY_LIMIT),
    "/v1/packs": ("pack_count", FREE_TIER_PACK_LIMIT),
}


class BillingGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not ENFORCE_TIER_LIMITS:
            return await call_next(request)

        if request.method == "GET":
            return await call_next(request)

        path = request.url.path
        path_normalized = path.rstrip("/")

        limit_info = None
        for prefix, info in RESOURCE_LIMITS.items():
            if path_normalized == prefix:
                limit_info = info
                break

        if limit_info is None:
            return await call_next(request)

        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            return await call_next(request)

        from ..db.database import get_async_session_factory
        from ..db.models import Tenant

        factory = get_async_session_factory()
        async with factory() as db:
            tenant = await db.get(Tenant, tenant_id)
            if not tenant or tenant.tier != "free":
                return await call_next(request)

            metric_key, limit = limit_info

            current_value = await self._get_current_usage(db, tenant_id, metric_key)
            if current_value >= limit:
                _log.warning(
                    "Tier limit exceeded: tenant=%d metric=%s current=%d limit=%d",
                    tenant_id, metric_key, current_value, limit,
                )
                return JSONResponse(
                    status_code=402,
                    content=TierLimitExceededResponse(
                        message=f"Free tier limiti asildi ({metric_key}: {current_value}/{limit}). Yükseltmek için /v1/billing/checkout adresini kullanin.",
                        upgrade_url="/v1/billing/checkout?tier=pro",
                        current_usage={metric_key: current_value},
                    ).model_dump(),
                )

        return await call_next(request)

    @staticmethod
    async def _get_current_usage(db, tenant_id: int, metric_key: str) -> int:
        """Return the tenant's usage for the current month."""
        from datetime import date
        from sqlalchemy import func, select

        from ..db.models import Entity, MetricPack, MeteringRecord

        today = date.today()
        start_of_month = date(today.year, today.month, 1)

        if metric_key == "sinyal_sayisi":
            result = await db.execute(
                select(func.coalesce(func.sum(MeteringRecord.sinyal_sayisi), 0)).where(
                    MeteringRecord.tenant_id == tenant_id,
                    MeteringRecord.tarih >= start_of_month,
                )
            )
            return result.scalar_one()
        elif metric_key == "entity_count":
            result = await db.execute(
                select(func.count()).select_from(Entity).where(
                    Entity.tenant_id == tenant_id,
                )
            )
            return result.scalar_one()
        elif metric_key == "pack_count":
            result = await db.execute(
                select(func.count()).select_from(MetricPack).where(
                    MetricPack.tenant_id == tenant_id,
                )
            )
            return result.scalar_one()

        return 0
