"""API authentication middleware — validates API tokens against configured keys.

Reads EVA_API_TOKEN from environment. When configured, all API routes require
X-API-Token header or ?token query parameter. Excludes /health, /metrics, /docs,
/redoc, /openapi.json, and /ws endpoints.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("eva.auth")

PUBLIC_EXACT_PATHS = {
    "/health/live",
    "/health/ready",
    "/metrics",
    "/metrics/prometheus",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/webhooks/github",  # authenticated by GitHub HMAC signature
}

PUBLIC_PREFIXES = (
    "/static/",
)

DEV_MODE_PUBLIC_WRITES = {
    "/api/chat",
    "/api/chat/stream",
    "/api/chat/sync",
    "/api/webhooks/github",
}


def _is_excluded(path: str) -> bool:
    return path in PUBLIC_EXACT_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates X-API-Token header or ?token query param on every request.

    When EVA_API_TOKEN is configured, all non-excluded routes require auth.
    When EVA_API_TOKEN is NOT set (dev mode), read-only endpoints pass through
    but write/execute endpoints still require a token — this prevents
    unauthenticated RCE and SSRF even in development.
    """

    def __init__(self, app: Any, token: str = "") -> None:
        super().__init__(app)
        self._token = token or os.environ.get("EVA_API_TOKEN", "")
        if self._token:
            logger.info("auth middleware enabled (token configured)")
        else:
            logger.warning(
                "EVA_API_TOKEN not set — read-only endpoints are open; "
                "write/execute endpoints still require auth"
            )

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Any:
        path = request.url.path

        if _is_excluded(path):
            return await call_next(request)

        # When a token is configured, all non-excluded routes require it
        if self._token:
            provided = request.headers.get("X-API-Token", "")
            if not provided:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "X-API-Token header required"},
                )
            if not _timing_safe_equals(provided, self._token):
                logger.warning("auth rejected from %s", request.client.host if request.client else "?")
                return JSONResponse(
                    status_code=403,
                    content={"detail": "invalid token"},
                )
            return await call_next(request)

        # Dev mode (no token configured): reads and chat are open, while every
        # other state-changing method is blocked. This deny-by-default rule
        # also protects newly-added endpoints without updating a path list.
        if (
            request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
            and path not in DEV_MODE_PUBLIC_WRITES
        ):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": (
                        "This endpoint requires authentication. "
                        "Set EVA_API_TOKEN and provide it via X-API-Token header."
                    ),
                },
            )

        return await call_next(request)


def _timing_safe_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)
