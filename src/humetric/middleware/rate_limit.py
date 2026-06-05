"""Rate-limit middleware — in-memory token bucket (tenant bazli)."""

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


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.buckets: dict[int, TokenBucket] = defaultdict(
            lambda: TokenBucket(limit=config.RATE_LIMIT_PER_MINUTE)
        )

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)

        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            return await call_next(request)

        bucket = self.buckets[int(tenant_id)]
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
