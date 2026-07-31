"""Unit tests for runtime.rate_limiter — SlidingWindowLimiter and middleware."""

from unittest.mock import MagicMock

import pytest

from runtime.rate_limiter import (
    RateLimitConfig,
    SlidingWindowLimiter,
    RateLimitMiddleware,
)


class TestRateLimitConfig:
    def test_defaults(self):
        cfg = RateLimitConfig()
        assert cfg.requests_per_minute == 60
        assert cfg.burst_multiplier == 2.0
        assert cfg.window_sec == 60.0

    def test_custom_values(self):
        cfg = RateLimitConfig(requests_per_minute=10, burst_multiplier=1.5, window_sec=30)
        assert cfg.requests_per_minute == 10
        assert cfg.burst_multiplier == 1.5


class TestSlidingWindowLimiter:
    def test_allow_within_limit(self):
        limiter = SlidingWindowLimiter(RateLimitConfig(requests_per_minute=100))
        for _ in range(50):
            assert limiter.allow("client-a") is True

    def test_deny_when_burst_exceeded(self):
        limiter = SlidingWindowLimiter(RateLimitConfig(requests_per_minute=1, burst_multiplier=1.0))
        assert limiter.allow("client-b") is True
        assert limiter.allow("client-b") is False  # burst exhausted

    def test_reset_clears_key(self):
        limiter = SlidingWindowLimiter(RateLimitConfig(requests_per_minute=1, burst_multiplier=1.0))
        assert limiter.allow("client-c") is True
        limiter.reset("client-c")
        assert limiter.allow("client-c") is True  # fresh after reset

    def test_stats_returns_active_keys(self):
        limiter = SlidingWindowLimiter()
        limiter.allow("client-d")
        stats = limiter.stats()
        assert stats["active_keys"] >= 1
        assert "config" in stats

    def test_different_keys_independent(self):
        limiter = SlidingWindowLimiter(RateLimitConfig(requests_per_minute=1, burst_multiplier=1.0))
        assert limiter.allow("a") is True
        assert limiter.allow("b") is True  # different key, not blocked

    def test_prune_stale_keys(self):
        limiter = SlidingWindowLimiter(RateLimitConfig(requests_per_minute=100))
        # Fill with many keys that have empty windows
        for i in range(10001):
            limiter._windows[f"stale-{i}"] = []
        # Next allow should trigger prune
        assert limiter.allow("real-client") is True
        assert len(limiter._windows) <= 10001  # pruned


class TestRateLimitMiddleware:
    def test_client_key_x_forwarded_for(self):
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "10.0.0.1, 10.0.0.2"}
        key = RateLimitMiddleware._client_key(request)
        assert key == "10.0.0.1"

    def test_client_key_direct_ip(self):
        request = MagicMock()
        request.headers = {}
        request.client.host = "192.168.1.1"
        key = RateLimitMiddleware._client_key(request)
        assert key == "192.168.1.1"

    def test_client_key_unknown(self):
        request = MagicMock()
        request.headers = {}
        request.client = None
        key = RateLimitMiddleware._client_key(request)
        assert key == "unknown"
