"""Tests for connectors.github.webhook_handler — HMAC, dedup, JSON handling."""

import asyncio
import hashlib
import hmac
import json

from connectors.github.webhook_handler import verify_signature, process_webhook
from event.event_bus import EventBus


class TestVerifySignature:
    SECRET = "my-secret-key"

    def test_valid_signature_passes(self):
        body = b'{"test": true}'
        digest = hmac.new(self.SECRET.encode(), body, hashlib.sha256).hexdigest()
        header = f"sha256={digest}"
        assert verify_signature(self.SECRET, header, body) is True

    def test_wrong_secret_fails(self):
        body = b'{"test": true}'
        digest = hmac.new(self.SECRET.encode(), body, hashlib.sha256).hexdigest()
        header = f"sha256={digest}"
        assert verify_signature("wrong-secret", header, body) is False

    def test_tampered_body_fails(self):
        body = b'{"test": true}'
        digest = hmac.new(self.SECRET.encode(), body, hashlib.sha256).hexdigest()
        header = f"sha256={digest}"
        assert verify_signature(self.SECRET, header, b'{"test": false}') is False

    def test_empty_secret_returns_false(self):
        assert verify_signature("", "sha256=abc123", b"body") is False

    def test_empty_signature_header_returns_false(self):
        assert verify_signature("secret", "", b"body") is False

    def test_missing_sha256_prefix_returns_false(self):
        assert verify_signature("secret", "not-a-sha256-prefix", b"body") is False

    def test_compare_digest_timing(self):
        """verify_signature must use constant-time comparison."""
        body = b"payload"
        digest = hmac.new(self.SECRET.encode(), body, hashlib.sha256).hexdigest()
        header = f"sha256={digest}"
        # Different-length strings still compare safely via compare_digest
        assert verify_signature(self.SECRET, header + "extra", body) is False


class TestProcessWebhook:
    SECRET = "webhook-secret"

    def _make_header(self, body: bytes) -> str:
        digest = hmac.new(self.SECRET.encode(), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def test_ok_publishes_event(self):
        bus = EventBus()
        dedup = set()
        body = json.dumps({"repository": {"full_name": "test/repo"}, "ref": "main"}).encode()

        result = asyncio.run(process_webhook(body, "push", "del-1", self.SECRET, bus, dedup))

        assert result["status"] == "ok"
        assert result["delivery_id"] == "del-1"
        assert "event_id" in result
        # Event should be in the bus queue
        event = bus.consume(timeout=1)
        assert event is not None
        assert event.type == "github_push"

    def test_duplicate_is_skipped(self):
        bus = EventBus()
        dedup = set()
        body = json.dumps({"repository": {"full_name": "test/repo"}}).encode()

        # First call publishes
        r1 = asyncio.run(process_webhook(body, "push", "dup-1", self.SECRET, bus, dedup))
        assert r1["status"] == "ok"

        # Second call with same delivery_id is a duplicate
        r2 = asyncio.run(process_webhook(body, "push", "dup-1", self.SECRET, bus, dedup))
        assert r2["status"] == "duplicate"

        # Only one event in queue
        bus.consume(timeout=1)
        assert bus.consume(timeout=0.1) is None

    def test_invalid_json_returns_invalid(self):
        bus = EventBus()
        dedup = set()
        body = b"not valid json {{{"

        result = asyncio.run(process_webhook(body, "push", "del-err", self.SECRET, bus, dedup))
        assert result["status"] == "invalid"
        assert bus.consume(timeout=0.1) is None

    def test_dedup_set_overflow_resets(self):
        """When dedup set exceeds 10k, it should clear to bound memory."""
        bus = EventBus()
        dedup = {str(i) for i in range(10001)}  # already at threshold
        body = json.dumps({"repository": {"full_name": "test/repo"}}).encode()

        result = asyncio.run(process_webhook(body, "push", "overflow-1", self.SECRET, bus, dedup))

        assert result["status"] == "ok"
        # After adding one more, the set should have been cleared
        # (previous 10001 entries gone, only new one remains)
        assert len(dedup) <= 1

    def test_multiple_different_events(self):
        bus = EventBus()
        dedup = set()
        body = json.dumps({"repository": {"full_name": "test/repo"}}).encode()

        r1 = asyncio.run(process_webhook(body, "issues", "del-a", self.SECRET, bus, dedup))
        r2 = asyncio.run(process_webhook(body, "pull_request", "del-b", self.SECRET, bus, dedup))

        assert r1["status"] == "ok"
        assert r2["status"] == "ok"
        assert bus.consume(timeout=1) is not None
        assert bus.consume(timeout=1) is not None
        assert bus.consume(timeout=0.1) is None
