"""Tier limit kontrol middleware'i — free tier sinyal/entity/pack limit asimi (Spec 026)."""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..config import (
    FREE_TIER_ENTITY_LIMIT,
    FREE_TIER_PACK_LIMIT,
    FREE_TIER_SIGNAL_LIMIT,
    HUMETRIC_BASE_URL,
)
from ..db.models import Tenant
from ..schema import error_envelope

_log = logging.getLogger(__name__)

RESOURCE_LIMITS = {
    "/v1/signals": ("signal", FREE_TIER_SIGNAL_LIMIT),
    "/v1/entities": ("entity", FREE_TIER_ENTITY_LIMIT),
    "/v1/packs": ("pack", FREE_TIER_PACK_LIMIT),
}


class BillingGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "GET":
            return await call_next(request)

        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            return await call_next(request)

        from ..db.database import get_async_session_factory
        from sqlalchemy import select

        factory = get_async_session_factory()
        async with factory() as db:
            tenant = await db.get(Tenant, tenant_id)
            if not tenant or tenant.tier != "free":
                return await call_next(request)

        return await call_next(request)
