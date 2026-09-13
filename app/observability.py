from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings


def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


logger = logging.getLogger("nexora")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach request id, log latency, expose X-Request-Id."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "request_failed method=%s path=%s request_id=%s duration_ms=%.1f",
                request.method,
                request.url.path,
                request_id,
                elapsed_ms,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
        if request.url.path.startswith("/api/"):
            logger.info(
                "request method=%s path=%s status=%s request_id=%s duration_ms=%.1f",
                request.method,
                request.url.path,
                response.status_code,
                request_id,
                elapsed_ms,
            )
        return response


class SimpleRateLimiter:
    """In-process sliding-window limiter (single-node)."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max_requests:
            return False
        q.append(now)
        return True


rate_limiter = SimpleRateLimiter(
    max_requests=settings.rate_limit_per_minute,
    window_seconds=60.0,
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        if request.url.path in {"/api/health", "/api/ready"}:
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        key = f"{client}:{request.url.path.split('/')[2] if len(request.url.path.split('/')) > 2 else 'api'}"
        if not rate_limiter.allow(key):
            return JSONResponse(
                {"detail": "Rate limit exceeded. Retry shortly."},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        return await call_next(request)
