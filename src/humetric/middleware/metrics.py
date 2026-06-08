"""Prometheus metrics middleware (Spec 026)."""

from __future__ import annotations

import time

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "humetric_http_requests_total",
    "Total number of HTTP requests",
    ["method", "path", "status"],
)

HTTP_LATENCY = Histogram(
    "humetric_http_request_duration_seconds",
    "HTTP request duration (seconds)",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

ACTIVE_REQUESTS = Gauge(
    "humetric_active_requests",
    "Number of currently active HTTP requests",
)

QUEUE_DEPTH = Gauge(
    "humetric_queue_depth",
    "Task queue depth",
    ["status"],
)

DB_POOL_SIZE = Gauge(
    "humetric_db_pool_size",
    "DB connection pool statistics",
    ["state"],
)


class PrometheusMiddleware:
    """ASGI middleware that collects metrics on every HTTP request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        ACTIVE_REQUESTS.inc()
        path = scope.get("path", "")
        method = scope.get("method", "")

        start = time.monotonic()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status = str(message.get("status", 500))
                HTTP_REQUESTS.labels(method=method, path=path, status=status).inc()
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.monotonic() - start
            HTTP_LATENCY.labels(method=method, path=path).observe(elapsed)
            ACTIVE_REQUESTS.dec()
