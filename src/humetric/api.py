"""HuMetric REST API Core — Spec 022 endpoints + Spec 021 foundation + Spec 026 registration/billing/metrics.

Run: uvicorn humetric.api:app --reload --port 8002
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import AsyncGenerator
from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import bcrypt as _bcrypt
from prometheus_client import make_asgi_app
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import config, kvkk
from .auth import hash_key
from .agents import ranker
from .config import AUTH_SECRET
from .db.database import get_async_session_factory, get_db, get_tenant_db
from .db.models import ApiKey, MeteringRecord, Task, Tenant
from .middleware.auth import AuthMiddleware
from .middleware.billing_guard import BillingGuardMiddleware
from .middleware.metrics import PrometheusMiddleware
from .middleware.rate_limit import RateLimitMiddleware
from .services.captcha_service import verify_captcha
from .services.email_service import send_verification_email, send_welcome_email
from .services.stripe_service import (
    create_checkout_session,
    create_customer,
    create_customer_portal_session,
    handle_webhook,
    verify_webhook_signature,
)
from .services.usage_service import (
    record_signal,
)
from .schema import (
    ApiKeyCreated,
    ApiKeyCreate,
    ApiKeyListResponse,
    ApiKeyRead,
    CheckoutResponse,
    ConsentCreate,
    ConsentRead,
    EntityCreate,
    EntityMetricRead,
    EntityMetricsResponse,
    EntityRead,
    ErrorResponse,
    PackCreate,
    PackDefinition,
    PackDetail,
    PackRead,
    PackWizardRequest,
    QueryRequest,
    QueryResponse,
    RankedResult,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    ReviewerOverrideRequest,
    ReviewerOverrideResponse,
    RotateApiKeyResponse,
    SignalCreate,
    SignalStatus,
    AuditLogListResponse,
    AuditLogRead,
    SignalTrace,
    TenantDashboardResponse,
    TenantKeysRead,
    TenantKeysUpdate,
    UsageRecordOut,
    UsageReportResponse,
    VerifyEmailResponse,
    error_envelope,
)
from .store import Store, _metric_row_to_read

_log = logging.getLogger(__name__)

__version__ = "1.0.0"

app = FastAPI(
    title="Humetric Platform API",
    version=__version__,
    description="Entity metric tracking and signal processing REST API",
    swagger_ui_parameters={"persistAuthorization": True},
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

V1_PREFIX = "/v1"


# ── Tenant session dependency ──────────────────────────────────

async def _get_tenant_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(status_code=401, detail={"error": {"code": "invalid_api_key", "message": "No tenant context"}})
    api_key_id = getattr(request.state, "api_key_id", 0)
    async for session in get_tenant_db(api_key_id, tenant_id):
        yield session


# ── Scope helper ───────────────────────────────────────────────

def _require_scope(request: Request, scope: str) -> None:
    scopes = getattr(request.state, "scopes", [])
    if scope not in scopes:
        raise HTTPException(
            status_code=403,
            detail=error_envelope("insufficient_scopes", f"Requires scope: {scope}").model_dump(),
        )


# ── Error handler ──────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope("internal_error", str(detail)).model_dump(),
    )


# ── Custom OpenAPI with Bearer auth (Spec 025) ─────────────────


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "API Key",
            "description": "HuMetric API key. Create via POST /v1/api-keys",
        }
    }
    schema["security"] = [{"bearerAuth": []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


# ── Health ─────────────────────────────────────────────────────

@app.get("/healthz", tags=["Health"])
async def healthz():
    return {"status": "ok", "service": "humetric", "version": __version__}


@app.get("/healthz/db", tags=["Health"])
async def healthz_db(db: AsyncSession = Depends(get_db)):
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        return {"status": "error", "database": str(exc)}


@app.get("/healthz/worker", tags=["Health"])
async def healthz_worker(db: AsyncSession = Depends(get_db)):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    queue_depth = await db.scalar(
        select(func.count()).select_from(Task).where(
            Task.status.in_(["queued", "processing"]),
        )
    )
    failed_last_hour = await db.scalar(
        select(func.count()).select_from(Task).where(
            Task.status == "failed",
            Task.created_at > one_hour_ago,
        )
    )
    oldest = await db.scalar(
        select(func.min(Task.created_at)).select_from(Task).where(
            Task.status == "queued",
        )
    )
    oldest_seconds = int((now - oldest).total_seconds()) if oldest else 0
    return {
        "workers": 1,
        "queue_depth": queue_depth or 0,
        "oldest_pending_seconds": oldest_seconds,
        "failed_last_hour": failed_last_hour or 0,
    }


# ── POST /v1/entities ──────────────────────────────────────────

@app.post(
    f"{V1_PREFIX}/entities",
    tags=["Entities"],
    status_code=201,
    responses={
        200: {"description": "Existing entity updated"},
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
async def create_entity(
    body: EntityCreate,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "entities:write")
    tenant_id = request.state.tenant_id

    existing = await Store.get_entity(db, body.id, tenant_id)
    is_new = existing is None

    if is_new:
        await Store.validate_entity_against_pack(db, tenant_id, body.entity_type, body.fields or {})
    else:
        await Store.check_entity_type_writable(db, tenant_id, body.entity_type)

    data = {
        "id": body.id,
        "tenant_id": tenant_id,
        "entity_type": body.entity_type,
        "fields": body.fields,
        "free_text": body.free_text or "",
        "status": body.status or "active",
    }

    if not is_new:
        data["fields"] = {**existing.fields, **body.fields} if body.fields else existing.fields

    entity = await Store.upsert_entity(db, data)

    if not is_new:
        response.status_code = 200

    metrics = await Store.get_entity_metrics(db, entity.id, tenant_id)
    return EntityRead(
        id=entity.id,
        entity_type=entity.entity_type,
        fields=entity.fields,
        free_text=entity.free_text,
        status=entity.status,
        metrics=[_entity_metric_to_read(m) for m in metrics],
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


# ── GET /v1/entities/{id} ─────────────────────────────────────

@app.get(
    f"{V1_PREFIX}/entities/{{entity_id}}",
    tags=["Entities"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def get_entity(
    entity_id: str,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "entities:read")
    tenant_id = request.state.tenant_id
    entity = await Store.get_entity(db, entity_id, tenant_id)
    if not entity:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("entity_not_found", f"Entity not found: {entity_id}").model_dump(),
        )

    metrics = await Store.get_entity_metrics(db, entity_id, tenant_id)
    metric_dicts = [_metric_row_to_read(m) for m in metrics]
    pack = await Store.get_active_pack_for_type(db, tenant_id, entity.entity_type)
    metric_dicts = await kvkk.filter_sensitive_metrics(
        metric_dicts, getattr(request.state, "scopes", []),
        pack=pack.definition if pack else None,
        db=db, entity_id=entity_id, tenant_id=tenant_id,
    )

    return EntityRead(
        id=entity.id,
        entity_type=entity.entity_type,
        fields=entity.fields,
        free_text=entity.free_text,
        status=entity.status,
        metrics=[EntityMetricRead(**m) for m in metric_dicts],
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


# ── GET /v1/entities/{id}/metrics ──────────────────────────────

@app.get(
    f"{V1_PREFIX}/entities/{{entity_id}}/metrics",
    tags=["Entities"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def get_entity_metrics_endpoint(
    entity_id: str,
    request: Request,
    include_history: bool = False,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "entities:read")
    tenant_id = request.state.tenant_id
    entity = await Store.get_entity(db, entity_id, tenant_id)
    if not entity:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("entity_not_found", f"Entity not found: {entity_id}").model_dump(),
        )

    metrics = await Store.get_entity_metrics(db, entity_id, tenant_id)
    metric_dicts = [_metric_row_to_read(m) for m in metrics]
    pack = await Store.get_active_pack_for_type(db, tenant_id, entity.entity_type)
    metric_dicts = await kvkk.filter_sensitive_metrics(
        metric_dicts, getattr(request.state, "scopes", []),
        pack=pack.definition if pack else None,
        db=db, entity_id=entity_id, tenant_id=tenant_id,
    )

    return EntityMetricsResponse(
        entity_id=entity_id,
        metrics=[EntityMetricRead(**m) for m in metric_dicts],
        metric_count=len(metric_dicts),
    )


# ── POST /v1/signals ──────────────────────────────────────────

@app.post(
    f"{V1_PREFIX}/signals",
    tags=["Signals"],
    status_code=202,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def create_signal(
    body: SignalCreate,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "signals:write")
    tenant_id = request.state.tenant_id

    entity = await Store.get_entity(db, body.entity_id, tenant_id)
    if not entity:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("entity_not_found", f"Entity not found: {body.entity_id}").model_dump(),
        )

    if entity.status == "archived":
        raise HTTPException(
            status_code=403,
            detail=error_envelope("entity_archived", f"Entity is archived: {body.entity_id}").model_dump(),
        )

    await Store.check_entity_type_writable(db, tenant_id, body.entity_type)

    # Idempotency-Key check
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if idempotency_key:
        existing = await Store.check_idempotency(db, tenant_id, idempotency_key, body.entity_id)
        if existing:
            metrics_read = []
            if existing.status == "completed" and existing.result:
                metrics_read = existing.result.get("metrics", [])
            return JSONResponse(
                status_code=200,
                content={
                    "signal_id": existing.id,
                    "status": existing.status,
                    "metrics": metrics_read,
                    "trace_url": f"/v1/signals/{existing.id}/trace",
                },
            )

    pack = await Store.get_active_pack_for_type(db, tenant_id, body.entity_type)
    pack_key = pack.pack_key if pack else None
    pack_version = pack.version if pack else None

    signal_id = str(uuid.uuid4())
    await Store.create_signal(db, {
        "id": signal_id,
        "tenant_id": tenant_id,
        "entity_id": body.entity_id,
        "entity_type": body.entity_type,
        "text": body.text or "",
        "structured": body.structured or {},
        "external_id": idempotency_key or body.external_id,
        "pack_key": pack_key,
        "pack_version": pack_version,
    })

    await Store.create_task(db, {
        "tenant_id": tenant_id,
        "signal_id": signal_id,
        "task_type": "signal_process",
        "status": "queued",
        "payload": {
            "entity_id": body.entity_id,
            "text": body.text,
            "entity_type": body.entity_type,
            "structured": body.structured or {},
            "pack_definition": pack.definition if pack else {},
        },
    })

    try:
        await record_signal(tenant_id)
    except Exception:
        _log.exception("Failed to record signal usage for tenant %d", tenant_id)

    return JSONResponse(
        status_code=202,
        content={
            "signal_id": signal_id,
            "status": "received",
            "trace_url": f"/v1/signals/{signal_id}/trace",
        },
    )


# ── GET /v1/signals/{id} ──────────────────────────────────────

@app.get(
    f"{V1_PREFIX}/signals/{{signal_id}}",
    tags=["Signals"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def get_signal(
    signal_id: str,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "signals:read")
    tenant_id = request.state.tenant_id
    row = await Store.get_signal(db, signal_id, tenant_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("signal_not_found", f"Signal not found: {signal_id}").model_dump(),
        )

    return SignalStatus(
        signal_id=row.id,
        status=row.status,
        entity_id=row.entity_id,
        error=row.error,
        created_at=row.created_at,
        processed_at=row.processed_at,
    )


# ── GET /v1/signals/{id}/trace ────────────────────────────────

@app.get(
    f"{V1_PREFIX}/signals/{{signal_id}}/trace",
    tags=["Signals"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def get_signal_trace(
    signal_id: str,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "signals:read")
    tenant_id = request.state.tenant_id
    row = await Store.get_signal(db, signal_id, tenant_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("signal_not_found", f"Signal not found: {signal_id}").model_dump(),
        )

    trace_data = row.result if row.result else {}
    entity = await Store.get_entity(db, row.entity_id, tenant_id)
    if entity:
        metrics = await Store.get_entity_metrics(db, row.entity_id, tenant_id)
        trace_data["entity_metrics"] = [_metric_row_to_read(m) for m in metrics]

    return SignalTrace(
        signal_id=row.id,
        entity_id=row.entity_id,
        trace_data=trace_data,
    )


# ── POST /v1/api-keys ─────────────────────────────────────────

@app.post(
    f"{V1_PREFIX}/api-keys",
    tags=["API Keys"],
    status_code=201,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def create_api_key(
    body: ApiKeyCreate,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    tenant_id = request.state.tenant_id
    creator_scopes = request.state.scopes or []

    requested_scopes = body.scopes if body.scopes else creator_scopes
    if set(requested_scopes) - set(creator_scopes):
        raise HTTPException(
            status_code=403,
            detail=error_envelope("insufficient_scopes", "Cannot exceed your own scopes").model_dump(),
        )

    expires_at = body.expires_at
    if body.expires_in_days and expires_at is None:
        expires_at = datetime.now(timezone.utc) + __import__("datetime").timedelta(days=min(body.expires_in_days, 730))

    full_key, api_key = await Store.create_api_key(
        db,
        tenant_id=tenant_id,
        prefix=body.prefix,
        label=body.label,
        scopes=requested_scopes,
        expires_at=expires_at,
    )

    await Store.audit(
        db,
        tenant_id=tenant_id,
        action="api_key.created",
        api_key_id=api_key.id,
        details={"label": body.label, "prefix": body.prefix, "scopes": requested_scopes},
    )

    return ApiKeyCreated(
        id=api_key.id,
        prefix=api_key.prefix,
        full_key=full_key,
        scopes=api_key.scopes,
        label=api_key.label,
    )


# ── GET /v1/api-keys ──────────────────────────────────────────

@app.get(
    f"{V1_PREFIX}/api-keys",
    tags=["API Keys"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def list_api_keys(
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    rows = await Store.list_api_keys(db, request.state.tenant_id)
    return ApiKeyListResponse(
        api_keys=[
            ApiKeyRead(
                id=r.id,
                prefix=r.prefix,
                scopes=r.scopes,
                label=r.label,
                is_revoked=r.is_revoked,
                last_used_at=r.last_used_at,
                created_at=r.created_at,
                expires_at=r.expires_at,
            )
            for r in rows
        ]
    )


# ── DELETE /v1/api-keys/{id} ──────────────────────────────────

@app.delete(
    f"{V1_PREFIX}/api-keys/{{key_id}}",
    tags=["API Keys"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def delete_api_key(
    key_id: int,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    if getattr(request.state, "api_key_id", None) is not None and key_id == request.state.api_key_id:
        raise HTTPException(
            status_code=403,
            detail=error_envelope("cannot_revoke_self", "Cannot revoke the API key used for this request. Create a new key first, then use it to revoke this one.").model_dump(),
        )

    from sqlalchemy import func
    active_count_result = await db.execute(
        select(func.count()).select_from(ApiKey).where(
            ApiKey.tenant_id == request.state.tenant_id,
            ApiKey.is_revoked == False,  # noqa: E712
        )
    )
    if active_count_result.scalar() <= 1:
        raise HTTPException(
            status_code=403,
            detail=error_envelope("cannot_revoke_last_key", "Cannot revoke the last active API key. Create a new key first.").model_dump(),
        )

    ok = await Store.revoke_api_key(db, key_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("api_key_not_found", f"API key not found: {key_id}").model_dump(),
        )
    await Store.audit(
        db,
        tenant_id=request.state.tenant_id,
        action="api_key.revoked",
        api_key_id=key_id,
        details={"revoked_by_key_id": request.state.api_key_id},
    )
    return {"status": "revoked", "id": key_id}


# ── GET /v1/audit-logs ────────────────────────────────────────

@app.get(
    f"{V1_PREFIX}/audit-logs",
    tags=["Audit"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def list_audit_logs(
    request: Request,
    action: str | None = None,
    entity_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "entities:read")
    tenant_id = request.state.tenant_id

    if limit > 500:
        limit = 500

    rows = await Store.list_audit_logs(
        db, tenant_id, action=action, entity_id=entity_id, limit=limit, offset=offset,
    )

    from sqlalchemy import func, select as _select
    from .db.models import AuditLog as _AuditLog
    count_q = _select(func.count()).select_from(_AuditLog).where(_AuditLog.tenant_id == tenant_id)
    if action:
        count_q = count_q.where(_AuditLog.action == action)
    if entity_id:
        count_q = count_q.where(_AuditLog.entity_id == entity_id)
    total = await db.scalar(count_q) or 0

    return AuditLogListResponse(
        items=[
            AuditLogRead(
                id=r.id,
                action=r.action,
                entity_id=r.entity_id,
                details=r.details,
                api_key_id=r.api_key_id,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── POST /v1/packs (Spec 023) ──────────────────────────────────

@app.post(
    f"{V1_PREFIX}/packs",
    tags=["Packs"],
    status_code=201,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def create_pack(
    body: PackCreate,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "packs:admin")
    tenant_id = request.state.tenant_id

    import yaml

    try:
        raw = yaml.safe_load(body.yaml_text)
    except yaml.YAMLError:
        raise HTTPException(
            status_code=422,
            detail=error_envelope("invalid_yaml", "Failed to parse YAML").model_dump(),
        )

    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=422,
            detail=error_envelope("invalid_yaml", "YAML must be a mapping").model_dump(),
        )

    try:
        parsed = PackDefinition.model_validate(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=error_envelope("validation_error", f"Invalid pack definition: {exc}").model_dump(),
        )

    pack_key = body.pack_key or raw.get("entity_type", "untitled")
    entity_type = parsed.entity_type

    existing = await Store.get_pack(db, tenant_id, pack_key)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=error_envelope(
                "pack_already_exists", f"Pack '{pack_key}' already exists. Use PUT to update."
            ).model_dump(),
        )

    existing_type_key, _ = await Store.entity_type_exists_in_active_pack(db, tenant_id, entity_type)
    if existing_type_key and existing_type_key != pack_key:
        raise HTTPException(
            status_code=409,
            detail=error_envelope(
                "entity_type_already_active",
                f"Entity type '{entity_type}' is already active in pack '{existing_type_key}'",
            ).model_dump(),
        )

    pack_def = parsed.model_dump(exclude_none=True)
    pack = await Store.create_pack(db, tenant_id, pack_key, parsed.version, pack_def)
    label = pack.definition.get("label", pack.pack_key)

    return PackRead(
        pack_key=pack.pack_key,
        version=pack.version,
        label=label,
        entity_type=pack.definition.get("entity_type", ""),
        is_active=pack.is_active,
        created_at=pack.created_at,
        updated_at=pack.updated_at,
    )


# ── GET /v1/packs ──────────────────────────────────────────────

@app.get(
    f"{V1_PREFIX}/packs",
    tags=["Packs"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def list_packs(
    request: Request,
    is_active: bool | None = None,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "packs:read")
    packs = await Store.list_packs(db, request.state.tenant_id, is_active=is_active)
    return [
        PackRead(
            pack_key=p.pack_key,
            version=p.version,
            label=p.definition.get("label", p.pack_key),
            entity_type=p.definition.get("entity_type", ""),
            is_active=p.is_active,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in packs
    ]


# ── GET /v1/packs/{key} ────────────────────────────────────────

@app.get(
    f"{V1_PREFIX}/packs/{{pack_key}}",
    tags=["Packs"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def get_pack(
    pack_key: str,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "packs:read")
    pack = await Store.get_pack(db, request.state.tenant_id, pack_key)
    if not pack:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("pack_not_found", f"Pack not found: {pack_key}").model_dump(),
        )
    return PackDetail(
        pack_key=pack.pack_key,
        version=pack.version,
        label=pack.definition.get("label", pack.pack_key),
        entity_type=pack.definition.get("entity_type", ""),
        is_active=pack.is_active,
        created_at=pack.created_at,
        updated_at=pack.updated_at,
        definition=pack.definition,
    )


# ── PUT /v1/packs/{key} ────────────────────────────────────────

@app.put(
    f"{V1_PREFIX}/packs/{{pack_key}}",
    tags=["Packs"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def update_pack(
    pack_key: str,
    body: PackCreate,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "packs:admin")
    tenant_id = request.state.tenant_id

    existing = await Store.get_pack(db, tenant_id, pack_key)
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("pack_not_found", f"Pack not found: {pack_key}").model_dump(),
        )

    import yaml

    try:
        raw = yaml.safe_load(body.yaml_text)
    except yaml.YAMLError:
        raise HTTPException(
            status_code=422,
            detail=error_envelope("invalid_yaml", "Failed to parse YAML").model_dump(),
        )

    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=422,
            detail=error_envelope("invalid_yaml", "YAML must be a mapping").model_dump(),
        )

    try:
        parsed = PackDefinition.model_validate(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=error_envelope("validation_error", f"Invalid pack definition: {exc}").model_dump(),
        )

    pack_def = parsed.model_dump(exclude_none=True)
    updated = await Store.update_pack(db, tenant_id, pack_key, pack_def)
    label = updated.definition.get("label", updated.pack_key)

    return PackRead(
        pack_key=updated.pack_key,
        version=updated.version,
        label=label,
        entity_type=updated.definition.get("entity_type", ""),
        is_active=updated.is_active,
        created_at=updated.created_at,
        updated_at=updated.updated_at,
    )


# ── POST /v1/packs/wizard (Spec 023) ──────────────────────────

@app.post(
    f"{V1_PREFIX}/packs/wizard",
    tags=["Packs"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def create_pack_wizard(
    body: PackWizardRequest,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "packs:admin")
    from .agents.wizard import generate_pack_yaml
    from .agents.base import get_tenant_llm_config

    try:
        _llm_provider, llm_key = await get_tenant_llm_config(request.state.tenant_id, db)
        result = await generate_pack_yaml(
            body.text, body.entity_type_hint,
            tenant_id=request.state.tenant_id,
            api_key=llm_key,
        )
        return result
    except Exception as exc:
        _log.exception("Wizard failed")
        raise HTTPException(
            status_code=503,
            detail=error_envelope("ai_service_unavailable", f"AI wizard service error: {exc}").model_dump(),
        )


# ── POST /v1/consent (Spec 023) ────────────────────────────────

@app.post(
    f"{V1_PREFIX}/consent",
    tags=["Consent"],
    status_code=201,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def create_consent(
    body: ConsentCreate,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "entities:write")
    tenant_id = request.state.tenant_id

    entity = await Store.get_entity(db, body.entity_id, tenant_id)
    if not entity:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("entity_not_found", f"Entity not found: {body.entity_id}").model_dump(),
        )

    status_val = body.status.value if hasattr(body.status, "value") else body.status or "granted"
    consent = await Store.create_consent(db, {
        "tenant_id": tenant_id,
        "entity_id": body.entity_id,
        "scope": body.scope,
        "status": status_val,
        "expires_at": body.expires_at,
    })

    await Store.audit(
        db,
        tenant_id=tenant_id,
        action="consent.granted" if status_val == "granted" else "consent.updated",
        entity_id=body.entity_id,
        api_key_id=request.state.api_key_id,
        details={"scope": body.scope, "status": status_val},
    )

    return ConsentRead(
        id=consent.id,
        entity_id=consent.entity_id,
        scope=consent.scope,
        status=consent.status,
        granted_at=consent.granted_at,
        revoked_at=consent.revoked_at,
        expires_at=consent.expires_at,
    )


# ── GET /v1/consent/{entity_id} (Spec 023) ─────────────────────

@app.get(
    f"{V1_PREFIX}/consent/{{entity_id}}",
    tags=["Consent"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def get_consents(
    entity_id: str,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "entities:read")
    tenant_id = request.state.tenant_id

    entity = await Store.get_entity(db, entity_id, tenant_id)
    if not entity:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("entity_not_found", f"Entity not found: {entity_id}").model_dump(),
        )

    consents = await Store.get_consents(db, entity_id, tenant_id)
    return [
        ConsentRead(
            id=c.id,
            entity_id=c.entity_id,
            scope=c.scope,
            status=c.status,
            granted_at=c.granted_at,
            revoked_at=c.revoked_at,
            expires_at=c.expires_at,
        )
        for c in consents
    ]


# ── DELETE /v1/consent/{entity_id} (Spec 023) ──────────────────

@app.delete(
    f"{V1_PREFIX}/consent/{{entity_id}}",
    tags=["Consent"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def delete_consent(
    entity_id: str,
    request: Request,
    scope: str | None = None,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "entities:write")
    tenant_id = request.state.tenant_id

    entity = await Store.get_entity(db, entity_id, tenant_id)
    if not entity:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("entity_not_found", f"Entity not found: {entity_id}").model_dump(),
        )

    if scope:
        ok = await Store.revoke_consent(db, entity_id, scope, tenant_id)
    else:
        count = await Store.revoke_all_consents(db, entity_id, tenant_id)
        ok = count > 0

    if ok:
        await Store.audit(
            db,
            tenant_id=tenant_id,
            action="consent.revoked",
            entity_id=entity_id,
            api_key_id=request.state.api_key_id,
            details={"scope": scope or "all"},
        )

    return {"revoked": ok, "entity_id": entity_id, "scope": scope or "all"}


# ── POST /v1/query ────────────────────────────────────────────

@app.post(
    f"{V1_PREFIX}/query",
    tags=["Query"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def query_entities(
    body: QueryRequest,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "query")
    tenant_id = request.state.tenant_id
    top_k = min(body.top_k, 100) if body.top_k else 10

    query_embedding = None
    if body.free_text_query and body.free_text_query.strip():
        from .embeddings import get_tenant_embedding_provider
        provider = await get_tenant_embedding_provider(tenant_id, db)
        query_embedding = (await provider.embed([body.free_text_query]))[0]

    candidates = await Store.hybrid_search_entities(
        db,
        tenant_id=tenant_id,
        query_embedding=query_embedding,
        query_text=body.free_text_query,
        entity_type=body.entity_type,
        filters=body.filters,
        top_k=max(top_k * 3, 20),
    )

    from .agents.base import get_tenant_llm_config
    llm_provider, llm_key = await get_tenant_llm_config(tenant_id, db)
    ranked = await ranker.rank_entities(
        candidates,
        query=body.free_text_query or body.rank_by or "",
        rank_by=body.rank_by,
        include_reasoning=body.include_reasoning,
        top_k=top_k,
        tenant_id=tenant_id,
        api_key=llm_key,
        provider=llm_provider,
    )

    results = []
    for r in ranked:
        entity = await Store.get_entity(db, r.entity_id, tenant_id)
        metrics_read = []
        if entity:
            metrics = await Store.get_entity_metrics(db, entity.id, tenant_id)
            metric_dicts = [_metric_row_to_read(m) for m in metrics]
            pack = await Store.get_active_pack_for_type(db, tenant_id, entity.entity_type)
            metric_dicts = await kvkk.filter_sensitive_metrics(
                metric_dicts, getattr(request.state, "scopes", []),
                pack=pack.definition if pack else None,
                db=db, entity_id=entity.id, tenant_id=tenant_id,
            )
            metrics_read = [EntityMetricRead(**m) for m in metric_dicts]

        results.append(
            RankedResult(
                entity_id=r.entity_id,
                entity_type=entity.entity_type if entity else "",
                score=r.score,
                metrics=metrics_read,
                reasoning=r.reasoning,
            )
        )

    return QueryResponse(results=results, top_k=top_k, model=config.CURATOR_MODEL)


# ── Helper ─────────────────────────────────────────────────────

def _entity_metric_to_read(m) -> EntityMetricRead:
    from .decay import decayed_confidence

    return EntityMetricRead(
        metric_key=m.metric_key,
        value=m.value,
        confidence=m.confidence,
        effective_confidence=decayed_confidence(m.confidence, m.last_updated),
        source_count=m.source_count,
        last_updated=m.last_updated,
        source_signal_id=m.signal_id,
    )


def _find_metric_def(pack_def: dict, metric_key: str) -> dict | None:
    """Find the metric definition in the pack definition."""
    for m in pack_def.get("metrics", []):
        if m.get("key") == metric_key:
            return m
    return None


# ── BYO-Key endpoints (Spec 025) ───────────────────────────────

@app.get(f"{V1_PREFIX}/tenant/keys", tags=["Tenant"])
async def get_tenant_keys(
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    tenant_id = request.state.tenant_id
    try:
        keys = await Store.get_tenant_keys(db, tenant_id)
    except RuntimeError:
        return JSONResponse(
            status_code=501,
            content=error_envelope("byo_key_unavailable", "BYO-key is not available (encryption key not configured)").model_dump(),
        )
    return TenantKeysRead(**keys)


@app.put(f"{V1_PREFIX}/tenant/keys", tags=["Tenant"])
async def upsert_tenant_keys(
    body: TenantKeysUpdate,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    tenant_id = request.state.tenant_id
    try:
        keys = await Store.upsert_tenant_keys(db, tenant_id, {
            "anthropic_key": body.anthropic_key,
            "voyage_key": body.voyage_key,
            "openai_key": body.openai_key,
            "google_ai_key": body.google_ai_key,
            "deepseek_key": body.deepseek_key,
            "llm_provider": body.llm_provider,
        })
    except RuntimeError:
        return JSONResponse(
            status_code=501,
            content=error_envelope("byo_key_unavailable", "BYO-key is not available (encryption key not configured)").model_dump(),
        )
    return TenantKeysRead(**keys)


@app.delete(f"{V1_PREFIX}/tenant/keys", tags=["Tenant"])
async def delete_tenant_keys(
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    tenant_id = request.state.tenant_id
    try:
        keys = await Store.delete_tenant_keys(db, tenant_id)
    except RuntimeError:
        return JSONResponse(
            status_code=501,
            content=error_envelope("byo_key_unavailable", "BYO-key is not available (encryption key not configured)").model_dump(),
        )
    return TenantKeysRead(**keys)


# ── Registration endpoints (Spec 026) ──────────────────────────

_serializer = URLSafeTimedSerializer(AUTH_SECRET)


@app.post(f"{V1_PREFIX}/register", tags=["Registration"])
async def register(body: RegisterRequest, request: Request):
    captcha_ok = await verify_captcha(body.captcha_token)
    if not captcha_ok:
        return JSONResponse(status_code=400, content=error_envelope("captcha_failed", "Captcha verification failed").model_dump())

    factory = get_async_session_factory()
    async with factory() as db:
        existing = await db.execute(select(Tenant).where(Tenant.email == body.email))
        if existing.scalar_one_or_none():
            return JSONResponse(status_code=409, content=error_envelope("email_already_registered", "This email is already registered").model_dump())

        auto_verify = not config.REQUIRE_EMAIL_VERIFICATION
        tenant = Tenant(
            code=f"t_{secrets.token_hex(6)}",
            name=body.email.split("@")[0],
            email=body.email,
            password_hash=_bcrypt.hashpw(body.password.encode()[:72], _bcrypt.gensalt()).decode(),
            email_verified=auto_verify,
            subscription_status="active" if auto_verify else "inactive",
            tier="free",
        )
        db.add(tenant)
        await db.flush()

        if auto_verify:
            # No email step: the tenant is active immediately. No API key is
            # minted here — users create their own keys from the dashboard,
            # where the full key is shown once at creation time.
            await db.commit()
            return JSONResponse(status_code=201, content=RegisterResponse(
                tenant_id=tenant.id,
                message="Registration complete. Create an API key from your dashboard.",
                email_verification_sent=False,
                email_verified=True,
            ).model_dump())

        await db.commit()

        token = _serializer.dumps(str(tenant.id))
        await send_verification_email(body.email, token)

        return JSONResponse(status_code=201, content=RegisterResponse(
            tenant_id=tenant.id,
            message="Verification email sent. Please check your inbox.",
        ).model_dump())


@app.get(f"{V1_PREFIX}/verify-email", tags=["Registration"])
async def verify_email(token: str):
    try:
        tenant_id_str = _serializer.loads(token, max_age=86400)
        tenant_id = int(tenant_id_str)
    except (SignatureExpired, BadSignature):
        return JSONResponse(status_code=400, content=error_envelope("invalid_verification_token", "Invalid or expired verification link").model_dump())

    factory = get_async_session_factory()
    async with factory() as db:
        await db.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tenant_id)},
        )
        tenant = await db.get(Tenant, tenant_id)
        if not tenant:
            return JSONResponse(status_code=404, content=error_envelope("tenant_not_found", "Tenant not found").model_dump())

        newly_verified = not tenant.email_verified

        if newly_verified:
            # First-time verification activates the tenant. No API key is
            # minted here — users create their own keys from the dashboard.
            tenant.email_verified = True
            tenant.subscription_status = "active"
            await send_welcome_email(tenant.email)

        await db.commit()
        return VerifyEmailResponse(
            verified=True,
            message="Email verified. Create an API key from your dashboard." if newly_verified else "Email verified.",
        ).model_dump()


# ── Dashboard login (email + password → session token) ──────────

@app.post(f"{V1_PREFIX}/login", tags=["Registration"])
async def login(body: LoginRequest):
    factory = get_async_session_factory()
    async with factory() as db:
        result = await db.execute(select(Tenant).where(Tenant.email == body.email))
        tenant = result.scalar_one_or_none()

    invalid = JSONResponse(
        status_code=401,
        content=error_envelope("invalid_credentials", "Invalid email or password").model_dump(),
    )
    if tenant is None or not tenant.password_hash:
        return invalid
    if not _bcrypt.checkpw(body.password.encode()[:72], tenant.password_hash.encode()):
        return invalid
    if not tenant.email_verified:
        return JSONResponse(
            status_code=403,
            content=error_envelope("email_not_verified", "Please verify your email before logging in").model_dump(),
        )

    token = _serializer.dumps({"tid": tenant.id, "t": "ds"})
    return LoginResponse(
        dashboard_token=token,
        tenant_id=tenant.id,
        name=tenant.name or tenant.email.split("@")[0],
        email=tenant.email,
    )


# ── Tenant self-service endpoints (Spec 026) ────────────────────

@app.get(f"{V1_PREFIX}/tenant/dashboard", tags=["Tenant"])
async def tenant_dashboard(
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    tenant_id = request.state.tenant_id
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=error_envelope("tenant_not_found", "Tenant not found").model_dump())

    today = date.today()
    first_of_month = today.replace(day=1)
    result = await db.execute(
        select(MeteringRecord).where(
            MeteringRecord.tenant_id == tenant_id,
            MeteringRecord.date >= first_of_month,
        )
    )
    records = result.scalars().all()

    usage = {"signal_count": sum(r.signal_count for r in records),
             "llm_token_count": sum(r.llm_token_count for r in records),
             "embedding_count": sum(r.embedding_count for r in records)}

    from .services.usage_service import TIER_LIMITS
    limits = TIER_LIMITS.get(tenant.tier, {})

    portal_url = None
    if tenant.stripe_customer_id:
        portal_url = await create_customer_portal_session(tenant.stripe_customer_id)

    return TenantDashboardResponse(
        tenant_id=tenant.id,
        tier=tenant.tier,
        subscription_status=tenant.subscription_status,
        api_key_prefix="hm_live",
        usage_current_month=usage,
        limits=limits,
        stripe_customer_portal_url=portal_url,
    ).model_dump()


@app.post(f"{V1_PREFIX}/tenant/rotate-api-key", tags=["Tenant"])
async def rotate_api_key(
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    tenant_id = request.state.tenant_id
    raw_key = f"hm_live_{secrets.token_urlsafe(32)}"
    api_key_hash = hash_key(raw_key)

    from datetime import timedelta
    from sqlalchemy import update

    await db.execute(update(ApiKey).where(ApiKey.tenant_id == tenant_id).values(is_revoked=True))
    api_key_row = ApiKey(
        tenant_id=tenant_id,
        prefix="hm_live",
        key_hash=api_key_hash,
        scopes=["signals:write", "entities:read", "entities:write", "signals:read", "query", "packs:read", "packs:admin"],
        label="Default API Key",
        expires_at=datetime.now(timezone.utc) + timedelta(days=730),
    )
    db.add(api_key_row)
    await db.commit()

    return RotateApiKeyResponse(api_key=raw_key, api_key_prefix="hm_live").model_dump()


# ── Billing endpoints (Spec 026) ─────────────────────────────────

@app.post(f"{V1_PREFIX}/billing/checkout", tags=["Billing"])
async def billing_checkout(
    tier: str,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    if tier not in ("pro", "enterprise"):
        raise HTTPException(status_code=400, detail=error_envelope("validation_error", "Tier must be 'pro' or 'enterprise'").model_dump())

    tenant_id = request.state.tenant_id
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=error_envelope("tenant_not_found", "Tenant not found").model_dump())

    if tenant.tier == tier:
        raise HTTPException(status_code=400, detail=error_envelope("validation_error", f"Already on {tier} tier").model_dump())

    if not tenant.stripe_customer_id and tenant.email:
        customer = await create_customer(tenant.email, tenant_id)
        tenant.stripe_customer_id = customer.id
        await db.commit()

    checkout_url = await create_checkout_session(tenant.stripe_customer_id, tier, tenant_id)
    return CheckoutResponse(checkout_url=checkout_url).model_dump()


@app.post(f"{V1_PREFIX}/billing/webhook", tags=["Billing"])
async def billing_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = await verify_webhook_signature(payload, sig_header)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid signature"})

    factory = get_async_session_factory()
    async with factory() as db:
        result = await handle_webhook(event, db)
    return {"received": True, **result}


@app.get(f"{V1_PREFIX}/usage", tags=["Usage"])
async def tenant_usage(
    start_date: str,
    end_date: str,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    tenant_id = request.state.tenant_id
    return await _build_usage_report(db, tenant_id, start_date, end_date)


@app.get(f"{V1_PREFIX}/admin/usage", tags=["Usage"])
async def admin_usage(
    tenant: str,
    start_date: str,
    end_date: str,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "packs:admin")
    return await _build_usage_report(db, int(tenant), start_date, end_date)


async def _build_usage_report(db: AsyncSession, tenant_id: int, start_date_str: str, end_date_str: str) -> dict:
    start_date = date.fromisoformat(start_date_str)
    end_date = date.fromisoformat(end_date_str)
    result = await db.execute(
        select(MeteringRecord).where(
            MeteringRecord.tenant_id == tenant_id,
            MeteringRecord.date >= start_date,
            MeteringRecord.date <= end_date,
        ).order_by(MeteringRecord.date)
    )
    records = result.scalars().all()

    record_list = [
        UsageRecordOut(date=str(r.date), signal_count=r.signal_count, llm_token_count=r.llm_token_count, embedding_count=r.embedding_count)
        for r in records
    ]
    total = UsageRecordOut(
        date=f"{start_date_str}..{end_date_str}",
        signal_count=sum(r.signal_count for r in records),
        llm_token_count=sum(r.llm_token_count for r in records),
        embedding_count=sum(r.embedding_count for r in records),
    )

    return UsageReportResponse(
        tenant_id=tenant_id, start_date=start_date_str, end_date=end_date_str,
        records=record_list, total=total,
    ).model_dump()


# ── Reviewer Override (eval/replay harness) ─────────────────────


@app.put(
    f"{V1_PREFIX}/metrics/{{entity_id}}/{{metric_key}}/review",
    tags=["Metrics"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def reviewer_override_metric(
    entity_id: str,
    metric_key: str,
    body: ReviewerOverrideRequest,
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "packs:admin")
    tenant_id = request.state.tenant_id

    previous = await Store.get_entity_metrics(db, entity_id, tenant_id)
    prev_metric = next((m for m in previous if m.metric_key == metric_key), None)

    updated = await Store.set_reviewer_override(
        db, entity_id, tenant_id, metric_key,
        value=body.value,
        confidence=body.confidence,
        comment=body.comment,
    )
    if not updated:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("signal_not_found", f"Metric not found: {entity_id}/{metric_key}").model_dump(),
        )

    return ReviewerOverrideResponse(
        entity_id=entity_id,
        metric_key=metric_key,
        previous_value=prev_metric.value if prev_metric else None,
        previous_confidence=prev_metric.confidence if prev_metric else None,
        new_value=body.value,
        new_confidence=body.confidence,
        comment=body.comment,
        overridden_at=datetime.now(timezone.utc),
    )


@app.get(
    f"{V1_PREFIX}/metrics/pending-review",
    tags=["Metrics"],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
    },
)
async def list_pending_reviews(
    request: Request,
    db: AsyncSession = Depends(_get_tenant_session),
):
    _require_scope(request, "packs:admin")
    tenant_id = request.state.tenant_id

    pending = await Store.list_pending_reviews(db, tenant_id)
    return [
        {
            "entity_id": m.entity_id,
            "metric_key": m.metric_key,
            "value": m.value,
            "confidence": m.confidence,
            "review_status": m.review_status,
            "signal_id": m.signal_id,
            "last_updated": m.last_updated.isoformat() if m.last_updated else None,
        }
        for m in pending
    ]


# ── Middleware chain ───────────────────────────────────────────

# Starlette executes middleware in the reverse of registration order (each
# add_middleware() call wraps the previous stack). Auth must run first so
# request.state.tenant_id exists before RateLimit/BillingGuard read it —
# registering Auth last here is what makes it execute first.
app.add_middleware(PrometheusMiddleware)
app.add_middleware(BillingGuardMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthMiddleware)


# ── Prometheus /metrics endpoint (auth atlanir) ────────────────

app.mount("/metrics", make_asgi_app())


def main():
    import uvicorn
    uvicorn.run("humetric.api:app", host="0.0.0.0", port=config.API_PORT, reload=True)
