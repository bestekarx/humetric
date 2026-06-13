"""API key auth middleware — Bearer token cozumleme."""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..db.database import get_admin_async_session_factory
from ..schema import error_envelope
from ..store import Store

_log = logging.getLogger(__name__)

PUBLIC_PATHS = {
    "/healthz",
    "/healthz/db",
    "/healthz/worker",
    "/metrics",
    "/v1/register",
    "/v1/billing/webhook",
    "/docs",
    "/docs/oauth2-redirect",
    "/openapi.json",
    "/redoc",
}


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    if path.startswith("/v1/verify-email"):
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/v1/verify-email"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content=error_envelope("invalid_api_key", "Missing or invalid Authorization header").model_dump(),
            )

        api_key = auth_header[7:].strip()
        key_prefix = api_key[:12] if len(api_key) > 12 else api_key
        factory = get_admin_async_session_factory()
        try:
            async with factory() as db:
                key_row = await Store.verify_and_get_api_key(db, api_key)
        except Exception as exc:
            _log.error("auth: DB error during key lookup prefix=%s err=%s", key_prefix, exc)
            return JSONResponse(
                status_code=503,
                content=error_envelope("service_unavailable", "Temporary auth error, please retry").model_dump(),
            )
        if key_row is None:
            _log.warning("auth: rejected key prefix=%s path=%s", key_prefix, request.url.path)
            return JSONResponse(
                status_code=401,
                content=error_envelope("invalid_api_key", "API key is invalid, revoked, or expired").model_dump(),
            )

        _log.debug("auth: accepted key id=%s tenant=%s path=%s", key_row.id, key_row.tenant_id, request.url.path)
        request.state.api_key_id = key_row.id
        request.state.tenant_id = key_row.tenant_id
        request.state.scopes = key_row.scopes or []

        response = await call_next(request)
        return response
