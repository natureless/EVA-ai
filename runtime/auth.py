"""API authentication middleware — validates API tokens against configured keys.

Reads EVA_API_TOKEN from environment. When configured, all API routes require
X-API-Token header or ?token query parameter. Excludes /health, /metrics, /docs,
/redoc, /openapi.json, and /ws endpoints.
"""

from __future__ import annotations

import os
import logging
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("eva.auth")

EXCLUDED_PREFIXES = (
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/ws",
    "/static",
)

# Endpoints that require auth even in dev mode (write/execute operations).
# Only enforced when EVA_API_TOKEN is NOT set — when a token IS configured,
# ALL non-excluded routes require auth.
DEV_MODE_REQUIRE_AUTH_PREFIXES = (
    "/api/executors/file/write",
    "/api/executors/file/list",
    "/api/executors/file/delete",
    "/api/executors/code/execute",
    "/api/executors/comms/log",
    "/api/executors/comms/notify",
    "/api/executors/comms/alert",
    "/api/executors/browser/",
    "/api/executors/api/",
    "/api/debug/snapshot/restore",
)


def _is_excluded(path: str) -> bool:
    return any(path.startswith(p) for p in EXCLUDED_PREFIXES)


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
            provided = request.headers.get("X-API-Token") or request.query_params.get("token", "")
            if not provided:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "X-API-Token header or ?token query parameter required"},
                )
            if not _timing_safe_equals(provided, self._token):
                logger.warning("auth rejected from %s", request.client.host if request.client else "?")
                return JSONResponse(
                    status_code=403,
                    content={"detail": "invalid token"},
                )
            return await call_next(request)

        # Dev mode (no token configured): read-only is open,
        # but write/execute endpoints are still blocked
        if any(path.startswith(p) for p in DEV_MODE_REQUIRE_AUTH_PREFIXES):
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
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0
