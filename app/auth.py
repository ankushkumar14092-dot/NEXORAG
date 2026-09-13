from __future__ import annotations

import secrets
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

# Public paths never require API key
_PUBLIC_PREFIXES = (
    "/api/health",
    "/api/ready",
    "/api/metrics",
    "/docs",
    "/openapi.json",
    "/redoc",
)


def _extract_key(request: Request) -> str:
    header = request.headers.get("x-api-key") or ""
    if header:
        return header.strip()
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def api_key_configured() -> bool:
    return bool((settings.api_key or "").strip())


def verify_api_key(provided: str) -> bool:
    expected = (settings.api_key or "").strip()
    if not expected:
        return True
    if not provided:
        return False
    return secrets.compare_digest(provided, expected)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """
    Optional API key gate.
    If API_KEY env is empty → open (local desk).
    If set → require X-API-Key / Bearer for /api/* except health/ready/metrics.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if any(path == p or path.startswith(p + "/") for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        if path in {"/api/health", "/api/ready", "/api/metrics"}:
            return await call_next(request)
        if not api_key_configured():
            return await call_next(request)
        if verify_api_key(_extract_key(request)):
            return await call_next(request)
        return JSONResponse(
            {"detail": "Unauthorized. Provide X-API-Key header."},
            status_code=401,
        )
