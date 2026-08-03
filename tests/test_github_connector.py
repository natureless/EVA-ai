"""Unit tests for connectors.github — normalizer, poller, webhook handler."""

import asyncio
import json
from unittest.mock import MagicMock


from connectors.github.event_normalizer import (
    normalize_webhook_payload,
    normalize_poller_payload,
    _build_source,
)
from connectors.github.webhook_handler import verify_signature, process_webhook
from event.event_bus import EventBus


class TestBuildSource:
    def test_full_name(self):
        assert _build_source({"full_name": "owner/repo"}) == "github:owner/repo"

    def test_empty_dict(self):
        assert _build_source({}) == "github"

    def test_none(self):
        assert _build_source(None) == "github"  # type: ignore[arg-type]

    def test_string_instead_of_dict(self):
        assert _build_source("not-a-dict") == "github"  # type: ignore[arg-type]


class TestNormalizeWebhook:
    def test_push_event(self):
        event = normalize_webhook_payload(
            "push", "delivery-001",
            {"repository": {"full_name": "org/repo"}, "ref": "refs/heads/main"},
        )
        assert event.type == "github_push"
        assert event.source == "github:org/repo"
        assert event.payload["_delivery_id"] == "delivery-001"
        assert event.payload["ref"] == "refs/heads/main"

    def test_pr_event(self):
        event = normalize_webhook_payload(
            "pull_request", "delivery-002",
            {"repository": {"full_name": "org/repo"}, "action": "opened"},
        )
        assert event.type == "github_pr"

    def test_issue_event(self):
        event = normalize_webhook_payload(
            "issues", "delivery-003",
            {"repository": {"full_name": "org/repo"}, "action": "created"},
        )
        assert event.type == "github_issue"

    def test_unknown_event_defaults_to_push(self):
        event = normalize_webhook_payload(
            "unknown_type", "delivery-004",
            {"repository": {"full_name": "org/repo"}},
        )
        assert event.type == "github_push"


class TestNormalizePoller:
    def test_pr_item(self):
        item = {"state": "open", "user": {"login": "alice"}, "updated_at": "2026-01-01T00:00:00Z"}
        event = normalize_poller_payload("pr", "org/repo", item)
        assert event.type == "github_pr"
        assert event.source == "github:org/repo"
        assert event.payload["action"] == "open"
        assert event.payload["sender"] == {"login": "alice"}

    def test_issue_item(self):
        item = {"state": "closed", "user": {"login": "bob"}}
        event = normalize_poller_payload("issue", "org/repo", item)
        assert event.type == "github_issue"
        assert event.payload["action"] == "closed"


class TestVerifySignature:
    def test_valid_signature(self):
        secret = "test-secret"
        body = b'{"action":"opened"}'
        import hashlib
        import hmac
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_signature(secret, sig, body) is True

    def test_wrong_secret(self):
        import hashlib
        import hmac
        body = b'{"action":"opened"}'
        # Sign with secret-A, verify with secret-B
        sig = "sha256=" + hmac.new(b"secret-a", body, hashlib.sha256).hexdigest()
        assert verify_signature("secret-b", sig, body) is False

    def test_empty_secret_returns_false(self):
        assert verify_signature("", "sha256=abc", b"body") is False

    def test_missing_prefix_returns_false(self):
        assert verify_signature("secret", "not-sha256=abc", b"body") is False

    def test_empty_header_returns_false(self):
        assert verify_signature("secret", "", b"body") is False


class TestProcessWebhook:
    def test_publishes_to_event_bus(self):
        async def _run():
            bus = MagicMock(spec=EventBus)
            dedup: set[str] = set()
            body = json.dumps({"repository": {"full_name": "org/repo"}, "action": "opened"}).encode()
            result = await process_webhook(body, "pull_request", "delivery-001", "", bus, dedup)
            assert result["status"] == "ok"
            bus.publish.assert_called_once()
        asyncio.run(_run())

    def test_dedup_skips_duplicate(self):
        async def _run():
            bus = MagicMock(spec=EventBus)
            dedup: set[str] = {"delivery-001"}
            body = json.dumps({"repository": {"full_name": "org/repo"}}).encode()
            result = await process_webhook(body, "push", "delivery-001", "", bus, dedup)
            assert result["status"] == "duplicate"
        asyncio.run(_run())

    def test_invalid_json_returns_invalid(self):
        async def _run():
            bus = MagicMock(spec=EventBus)
            dedup: set[str] = set()
            result = await process_webhook(b"not-json", "push", "delivery-002", "", bus, dedup)
            assert result["status"] == "invalid"
        asyncio.run(_run())

    def test_dedup_clears_when_too_large(self):
        async def _run():
            bus = MagicMock(spec=EventBus)
            dedup: set[str] = set(str(i) for i in range(10001))
            body = json.dumps({"repository": {"full_name": "org/repo"}}).encode()
            result = await process_webhook(body, "push", "new-delivery", "", bus, dedup)
            assert result["status"] == "ok"
            assert len(dedup) <= 2
        asyncio.run(_run())


class TestGitHubPoller:
    def test_start_creates_daemon_thread(self):
        bus = MagicMock(spec=EventBus)
        poller = __import__("connectors.github.poller", fromlist=["GitHubPoller"]).GitHubPoller(
            bus, token="fake", repos=["a/b"],
        )
        poller.start()
        assert poller._thread is not None
        assert poller._thread.daemon is True
        poller.stop()

    def test_start_is_idempotent(self):
        bus = MagicMock(spec=EventBus)
        poller = __import__("connectors.github.poller", fromlist=["GitHubPoller"]).GitHubPoller(
            bus, token="fake", repos=["a/b"],
        )
        poller.start()
        t1 = poller._thread
        poller.start()
        assert poller._thread is t1
        poller.stop()

    def test_stop_clears_flag(self):
        bus = MagicMock(spec=EventBus)
        poller = __import__("connectors.github.poller", fromlist=["GitHubPoller"]).GitHubPoller(
            bus, token="fake", repos=["a/b"],
        )
        poller.start()
        poller.stop()
        assert poller._stop.is_set()

    def test_init_poll_checkpoints(self):
        bus = MagicMock(spec=EventBus)
        poller = __import__("connectors.github.poller", fromlist=["GitHubPoller"]).GitHubPoller(
            bus, token="fake", repos=["a/b", "c/d"],
        )
        poller._init_poll_checkpoints()
        assert "a/b" in poller._last_polled
        assert "c/d" in poller._last_polled

    def test_advance_checkpoint_returns_newest(self):
        bus = MagicMock(spec=EventBus)
        poller = __import__("connectors.github.poller", fromlist=["GitHubPoller"]).GitHubPoller(
            bus, token="fake", repos=["a/b"],
        )
        items = [
            {"updated_at": "2026-01-01T00:00:00Z"},
            {"updated_at": "2026-06-01T00:00:00Z"},
        ]
        newest = poller._advance_checkpoint("a/b", items, "2025-01-01T00:00:00Z")
        assert newest == "2026-06-01T00:00:00Z"

    def test_is_publishable_after_checkpoint(self):
        bus = MagicMock(spec=EventBus)
        poller = __import__("connectors.github.poller", fromlist=["GitHubPoller"]).GitHubPoller(
            bus, token="fake", repos=["a/b"],
        )
        item = {"updated_at": "2026-01-01T00:00:00Z"}
        assert poller._is_publishable(item, "2025-01-01T00:00:00Z", "pulls") is True

    def test_is_publishable_before_checkpoint_skipped(self):
        bus = MagicMock(spec=EventBus)
        poller = __import__("connectors.github.poller", fromlist=["GitHubPoller"]).GitHubPoller(
            bus, token="fake", repos=["a/b"],
        )
        item = {"updated_at": "2024-01-01T00:00:00Z"}
        assert poller._is_publishable(item, "2025-01-01T00:00:00Z", "pulls") is False

    def test_is_publishable_pr_in_issues_skipped(self):
        bus = MagicMock(spec=EventBus)
        poller = __import__("connectors.github.poller", fromlist=["GitHubPoller"]).GitHubPoller(
            bus, token="fake", repos=["a/b"],
        )
        item = {"updated_at": "2026-01-01T00:00:00Z", "pull_request": {}}
        assert poller._is_publishable(item, "2025-01-01T00:00:00Z", "issues") is False

    def test_publish_poller_item(self):
        bus = MagicMock(spec=EventBus)
        poller = __import__("connectors.github.poller", fromlist=["GitHubPoller"]).GitHubPoller(
            bus, token="fake", repos=["a/b"],
        )
        item = {"state": "open", "user": {"login": "alice"}, "number": 1}
        poller._publish_poller_item(item, "pulls", "a/b")
        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert event.type == "github_pr"
