"""Unit tests for ResultRegistry — create, fulfill, wait, pop, peek, cleanup."""

import time


from runtime.result_registry import ResultRegistry


class TestResultRegistry:
    def test_create_and_size(self):
        reg = ResultRegistry()
        reg.create("corr-1")
        assert reg.size() == 1

    def test_fulfill_and_pop(self):
        reg = ResultRegistry()
        reg.create("corr-1")
        reg.fulfill("corr-1", {"ok": True})
        result = reg.pop("corr-1")
        assert result == {"ok": True}
        assert reg.size() == 0

    def test_fulfill_nonexistent_noop(self):
        reg = ResultRegistry()
        reg.fulfill("nonexistent", {"ok": True})
        assert reg.size() == 0

    def test_peek_returns_payload(self):
        reg = ResultRegistry()
        reg.create("corr-1")
        reg.fulfill("corr-1", {"data": 42})
        result = reg.peek("corr-1")
        assert result == {"data": 42}
        # peek does not remove
        assert reg.size() == 1

    def test_peek_nonexistent_returns_none(self):
        reg = ResultRegistry()
        assert reg.peek("missing") is None

    def test_wait_already_fulfilled(self):
        reg = ResultRegistry()
        reg.create("corr-1")
        reg.fulfill("corr-1", {"ready": True})
        result = reg.wait("corr-1", timeout=1.0)
        assert result == {"ready": True}

    def test_wait_nonexistent_returns_none(self):
        reg = ResultRegistry()
        assert reg.wait("missing", timeout=0.1) is None

    def test_cleanup_removes_expired(self):
        reg = ResultRegistry()
        reg.create("corr-1")
        # Manually set created_at far in the past
        reg._pending["corr-1"].created_at = time.time() - 120
        removed = reg.cleanup(ttl_sec=60)
        assert removed == 1
        assert reg.size() == 0

    def test_cleanup_keeps_recent(self):
        reg = ResultRegistry()
        reg.create("corr-1")
        removed = reg.cleanup(ttl_sec=60)
        assert removed == 0
        assert reg.size() == 1

    def test_pop_nonexistent_returns_none(self):
        reg = ResultRegistry()
        assert reg.pop("missing") is None
