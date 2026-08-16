"""Usage-recording middleware — one usage_record row per authenticated request.

``usage_record`` has existed since Spec 022 with the right columns and an RLS
policy, but nothing ever wrote to it. This middleware is that writer.

Two design constraints shaped it:

*Never slow the request down.* Rows go onto a bounded in-process queue and a
single background task flushes them in batches. If the queue is full the row is
dropped with a warning — measurement must never add latency to, or fail, a real
request.

*Never trust the client for identity.* ``tenant_id`` and ``api_key_id`` come
from what AuthMiddleware resolved off the bearer token. The ``X-HuMetric-*``
headers only supply descriptive fields (which surface, which tool), are
whitelist-validated, and at worst let a tenant garble its own report.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ..db.database import get_admin_async_session_factory
from ..db.models import UsageRecord
from .auth import _is_public

_log = logging.getLogger(__name__)

# Descriptive headers set by first-party clients (see mcp_server.py).
CLIENT_HEADER = "X-HuMetric-Client"
TOOL_HEADER = "X-HuMetric-Tool"
CALL_ID_HEADER = "X-HuMetric-Call-Id"

# A closed set, so an arbitrary header value cannot invent a new channel and
# fragment every report that groups by it.
KNOWN_CLIENTS = frozenset({"mcp", "rest", "dashboard"})

_TOOL_RE = re.compile(r"^[a-z0-9_]{1,64}$")

# Bounded on purpose: an unbounded queue turns a database stall into unbounded
# memory growth in the API process.
_QUEUE_MAXSIZE = 1000
_BATCH_SIZE = 100
_FLUSH_INTERVAL_S = 1.0

_queue: asyncio.Queue[dict] | None = None
_flusher: asyncio.Task | None = None
# The flusher task belongs to the loop it was created on. Production has one
# loop for the process lifetime, but a test suite creates a fresh loop per
# test — without this the task would stay bound to a closed loop and every
# later row would queue up forever, never written.
_flusher_loop: asyncio.AbstractEventLoop | None = None


def _normalise_client(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().lower()
    return value if value in KNOWN_CLIENTS else None


def _normalise_tool(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    return value if _TOOL_RE.match(value) else None


def _normalise_call_id(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.strip()[:64] or None


def _endpoint_template(request: Request) -> str:
    """Prefer the route template over the raw path.

    ``/v1/entities/acme-corp/metrics`` as a literal makes every entity its own
    endpoint and no grouping is possible; ``/v1/entities/{entity_id}/metrics``
    aggregates. Falls back to the raw path for unmatched routes (404s).
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    return path[:100]


def _ensure_flusher() -> asyncio.Queue:
    """Create the queue and start the flusher on first use.

    api.py builds its FastAPI app without a lifespan hook, so there is no
    startup event to hang this off. Lazy start keeps the app construction
    untouched and binds the task to the running loop that actually serves
    requests.
    """
    global _queue, _flusher, _flusher_loop
    loop = asyncio.get_running_loop()
    if _queue is None or _flusher_loop is not loop:
        _queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    if _flusher is None or _flusher.done() or _flusher_loop is not loop:
        _flusher = asyncio.create_task(_flush_loop(_queue))
        _flusher_loop = loop
    return _queue


async def _flush_loop(queue: asyncio.Queue) -> None:
    while True:
        batch: list[dict] = []
        try:
            batch.append(await queue.get())
            # Drain whatever else is already waiting, then write once.
            while len(batch) < _BATCH_SIZE:
                try:
                    batch.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await _write_batch(batch)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed write loses this batch, never the request that produced
            # it — usage data is expendable, the API call is not.
            _log.exception("usage flush failed; %d row(s) dropped", len(batch))
        await asyncio.sleep(_FLUSH_INTERVAL_S)


async def _write_batch(rows: list[dict]) -> None:
    """Insert with the admin session.

    RLS is bypassed deliberately: this is a system-owned audit path writing on
    behalf of many tenants in one batch, and tenant_id is set explicitly on
    every row. AuthMiddleware uses the same factory for the same reason.
    """
    factory = get_admin_async_session_factory()
    async with factory() as db:
        db.add_all([UsageRecord(**row) for row in rows])
        await db.commit()


class UsageMiddleware(BaseHTTPMiddleware):
    """Records every authenticated request. Must run *inside* AuthMiddleware so
    ``request.state`` is already populated — see the ordering note in api.py."""

    async def dispatch(self, request: Request, call_next):
        if _is_public(request.url.path):
            return await call_next(request)

        started = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - started) * 1000)

        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            # Auth rejected the request; there is no tenant to attribute it to.
            return response

        row = {
            "tenant_id": int(tenant_id),
            "api_key_id": getattr(request.state, "api_key_id", None),
            "endpoint": _endpoint_template(request),
            "method": request.method[:10],
            "status_code": response.status_code,
            "client": _normalise_client(request.headers.get(CLIENT_HEADER)),
            "tool_name": _normalise_tool(request.headers.get(TOOL_HEADER)),
            "call_id": _normalise_call_id(request.headers.get(CALL_ID_HEADER)),
            "duration_ms": duration_ms,
        }

        try:
            _ensure_flusher().put_nowait(row)
        except asyncio.QueueFull:
            _log.warning("usage queue full; dropping record for tenant %s", tenant_id)
        except Exception:
            _log.exception("failed to enqueue usage record for tenant %s", tenant_id)

        return response
