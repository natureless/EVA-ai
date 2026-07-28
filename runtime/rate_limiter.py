"""In-memory rate limiter with sliding-window counters.

Provides per-IP and per-endpoint rate enforcement. Limits are loaded
from constitution.yaml boundaries at bootstrap and can be overridden
per route via the @rate_limit decorator or middleware config.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


@dataclass
class RateLimitConfig:
    requests_per_minute: int = 60
    burst_multiplier: float = 2.0  # allow short bursts at 2x rate
    window_sec: float = 60.0


class SlidingWindowLimiter:
    """Per-key sliding window rate limiter.

    Each key (e.g. client IP) gets a ring of timestamps. On each
    check, we expire old entries and count remaining.
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()
        self._lock = threading.Lock()
        self._windows: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str, cost: int = 1) -> bool:
        """Return True if the request should be allowed."""
        now = time.time()
        cutoff = now - self.config.window_sec
        burst_limit = int(self.config.requests_per_minute * self.config.burst_multiplier)

        with self._lock:
            window = self._windows[key]

            # expire old timestamps
            while window and window[0] < cutoff:
                window.pop(0)

            if len(window) + cost > burst_limit:
                return False

            for _ in range(cost):
                window.append(now)

            # prune stale keys occasionally
            if len(self._windows) > 10_000:
                stale = [k for k, w in self._windows.items() if not w]
                for k in stale:
                    del self._windows[k]

            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._windows.pop(key, None)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            active = sum(1 for w in self._windows.values() if w)
            return {
                "active_keys": active,
                "total_keys": len(self._windows),
                "config": {
                    "requests_per_minute": self.config.requests_per_minute,
                    "window_sec": self.config.window_sec,
                },
            }


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that rate-limits requests by client IP.

    Wired into the app at bootstrap. Limits are per-IP with a sliding
    window. Health and metrics endpoints are excluded.
    """

    def __init__(
        self,
        app: ASGIApp,
        limiter: SlidingWindowLimiter,
        *,
        exclude_paths: set[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.limiter = limiter
        self.exclude_paths = exclude_paths or {"/health/live", "/health/ready", "/metrics"}

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        if request.url.path in self.exclude_paths:
            return await call_next(request)  # type: ignore[no-any-return]  # type: ignore[no-any-return]

        key = self._client_key(request)
        if not self.limiter.allow(key):
            return Response(
                content='{"detail":"rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(int(self.limiter.config.window_sec))},
            )

        return await call_next(request)  # type: ignore[no-any-return]

    @staticmethod
    def _client_key(request: Request) -> str:
        # Prefer X-Forwarded-For for proxied deployments
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        host = request.client.host if request.client else "unknown"
        return host
