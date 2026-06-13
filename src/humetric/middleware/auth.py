"""API key auth middleware — Bearer token cozumleme."""

from __future__ import annotations

import logging

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..config import AUTH_SECRET
from ..db.database import get_admin_async_session_factory
from ..schema import error_envelope
from ..store import Store

_log = logging.getLogger(__name__)
_serializer = URLSafeTimedSerializer(AUTH_SECRET)

# Scopes granted to dashboard session tokens (email+password login).
_DASHBOARD_SESSION_SCOPES = [
    "signals:write", "entities:read", "entities:write",
    "signals:read", "query", "packs:read", "packs:admin",
]

PUBLIC_PATHS = {
    "/healthz",
    "/healthz/db",
    "/healthz/worker",
    "/metrics",
    "/v1/register",
    "/v1/login",
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

        token = auth_header[7:].strip()

        # Try dashboard session token first (signed by _serializer, max 24 h).
        try:
            payload = _serializer.loads(token, max_age=86400)
            if isinstance(payload, dict) and payload.get("t") == "ds":
                request.state.api_key_id = None
                request.state.tenant_id = int(payload["tid"])
                request.state.scopes = _DASHBOARD_SESSION_SCOPES
                _log.debug(
                    "auth: accepted dashboard session tenant=%s path=%s",
                    request.state.tenant_id, request.url.path,
                )
                return await call_next(request)
        except (SignatureExpired, BadSignature, KeyError, TypeError, ValueError):
            pass  # fall through to API key check

        # API key path.
        key_prefix = token[:12] if len(token) > 12 else token
        factory = get_admin_async_session_factory()
        try:
            async with factory() as db:
                key_row, failure_reason = await Store.verify_and_get_api_key(db, token)
                if failure_reason is not None and key_row is not None:
                    # revoked or expired — we have tenant context, write DB audit
                    await Store.audit(
                        db,
                        tenant_id=key_row.tenant_id,
                        action="auth.rejected",
                        api_key_id=key_row.id,
                        details={
                            "reason": failure_reason,
                            "path": request.url.path,
                            "method": request.method,
                        },
                    )
        except Exception as exc:
            _log.error("auth: DB error during key lookup prefix=%s err=%s", key_prefix, exc)
            return JSONResponse(
                status_code=503,
                content=error_envelope("service_unavailable", "Temporary auth error, please retry").model_dump(),
            )

        if failure_reason is not None:
            _log.warning("auth: rejected key prefix=%s reason=%s path=%s", key_prefix, failure_reason, request.url.path)
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
