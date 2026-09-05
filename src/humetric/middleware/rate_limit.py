"""Rate-limit middleware — in-memory token bucket (tenant bazli).

Also enforces an IP-keyed token bucket on a small set of pre-auth paths
(IP_LIMITED_PATHS below) that never get a tenant_id from AuthMiddleware —
e.g. /v1/register and /v1/login. request.client.host only reflects the real
client when uvicorn trusts forwarded headers from the reverse proxy (see
Dockerfile.prod's --proxy-headers); without that, all pre-auth traffic
behind Traefik shares one internal-hop IP and this limit is effectively
applied to the whole site at once, not per attacker.
"""

from __future__ import annotations

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import config
from ..schema import error_envelope


class TokenBucket:
    def __init__(self, limit: int, window_s: int = 60):
        self.limit = limit
        self.window_s = window_s
        self.tokens = limit
        self.last_refill = time.monotonic()

    def try_consume(self) -> tuple[bool, int, int]:
        now = time.monotonic()
        elapsed = now - self.last_refill
        refill = int(elapsed / self.window_s * self.limit)
        if refill > 0:
            self.tokens = min(self.limit, self.tokens + refill)
            self.last_refill = now

        if self.tokens > 0:
            self.tokens -= 1
            return True, self.tokens, int(self.window_s - elapsed + self.last_refill)

        return False, 0, int(self.window_s - elapsed + self.last_refill)


# Paths with no tenant_id (pre-auth, see AuthMiddleware.PUBLIC_PATHS) that
# still need abuse protection: path -> (limit, window_s).
IP_LIMITED_PATHS: dict[str, tuple[int, int]] = {
    "/v1/register": (config.REGISTER_RATE_LIMIT_PER_HOUR, 3600),
    "/v1/login": (config.LOGIN_RATE_LIMIT_PER_HOUR, 3600),
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.buckets: dict[int, TokenBucket] = defaultdict(
            lambda: TokenBucket(limit=config.RATE_LIMIT_PER_MINUTE)
        )
        # Keyed by (path, ip) rather than defaultdict since each path in
        # IP_LIMITED_PATHS carries its own limit/window. Never evicted —
        # same accepted-risk shape as self.buckets above (each entry is
        # tiny; not worth an LRU/TTL until it's an observed problem).
        self.ip_buckets: dict[tuple[str, str], TokenBucket] = {}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/healthz":
            return await call_next(request)

        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is not None:
            bucket = self.buckets[int(tenant_id)]
        elif path in IP_LIMITED_PATHS:
            ip = request.client.host if request.client else "unknown"
            key = (path, ip)
            if key not in self.ip_buckets:
                limit, window_s = IP_LIMITED_PATHS[path]
                self.ip_buckets[key] = TokenBucket(limit=limit, window_s=window_s)
            bucket = self.ip_buckets[key]
        else:
            return await call_next(request)

        ok, remaining, reset_s = bucket.try_consume()

        if not ok:
            retry_after = max(1, reset_s)
            return JSONResponse(
                status_code=429,
                content=error_envelope(
                    "rate_limit_exceeded",
                    f"Rate limit exceeded. Retry after {retry_after}s",
                ).model_dump(),
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(bucket.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(bucket.limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        response.headers["X-RateLimit-Reset"] = str(reset_s)
        return response
