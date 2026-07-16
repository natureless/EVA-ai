"""API authentication middleware — validates API tokens against configured keys.

Reads EVA_API_TOKEN from environment. When configured, all API routes require
X-API-Token header or ?token query parameter. Excludes /health, /metrics, /docs,
/redoc, /openapi.json, and /ws endpoints.
"""

from __future__ import annotations

import os
import logging
from typing import Callable

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


def _is_excluded(path: str) -> bool:
    return any(path.startswith(p) for p in EXCLUDED_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates X-API-Token header or ?token query param on every request.

    If EVA_API_TOKEN env is not set, all requests pass through (dev mode).
    """

    def __init__(self, app, token: str = "") -> None:
        super().__init__(app)
        self._token = token or os.environ.get("EVA_API_TOKEN", "")
        if self._token:
            logger.info("auth middleware enabled (token configured)")
        else:
            logger.warning("EVA_API_TOKEN not set — API is open (no auth)")

    async def dispatch(self, request: Request, call_next: Callable):
        if not self._token or _is_excluded(request.url.path):
            return await call_next(request)

        provided = request.headers.get("X-API-Token") or request.query_params.get("token", "")
        if not provided:
            return JSONResponse(
                status_code=401,
                content={"detail": "X-API-Token header or ?token query parameter required"},
            )

        # constant-time comparison to avoid timing side channels
        if not _timing_safe_equals(provided, self._token):
            logger.warning("auth rejected from %s", request.client.host if request.client else "?")
            return JSONResponse(
                status_code=403,
                content={"detail": "invalid token"},
            )

        return await call_next(request)


def _timing_safe_equals(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0
