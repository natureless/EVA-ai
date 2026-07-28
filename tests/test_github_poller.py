"""Tests for connectors.github.poller — checkpoint, lifecycle, filtering."""

import json

from connectors.github.poller import GitHubPoller
from event.event_bus import EventBus


class _FakeUrlOpen:
    """A module-like object that fakes urllib.request for poller tests."""

    def __init__(self, items_by_url: dict[str, list[dict]], default=None):
        self._items = items_by_url
        self._default = default

    class Request:
        def __init__(self, url, headers=None):
            self.full_url = url
            self.headers = headers or {}

    class _Response:
        def __init__(self, data):
            self._data = json.dumps(data).encode()
        def read(self):
            return self._data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def urlopen(self, req, timeout=15):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for key, items in self._items.items():
            if key in url:
                return self._Response(items)
        if self._default is not None:
            return self._Response(self._default)
        return self._Response([])


class TestGitHubPoller:
    def _make_poller(self, repos=None, interval=300):
        return GitHubPoller(
            event_bus=EventBus(),
            token="fake-token",
            repos=repos or ["test-org/test-repo"],
            interval_sec=interval,
        )

    # ── lifecycle ─────────────────────────────────────────

    def test_start_creates_daemon_thread(self):
        poller = self._make_poller()
        poller._poll_once = lambda: None  # prevent real HTTP call
        poller.start()
        assert poller._thread is not None
        assert poller._thread.daemon is True
        poller.stop()

    def test_start_is_idempotent(self):
        poller = self._make_poller()
        poller._poll_once = lambda: None
        poller.start()
        t1 = poller._thread
        poller.start()
        assert poller._thread is t1
        poller.stop()

    def test_stop_clears_flag(self):
        poller = self._make_poller()
        poller._poll_once = lambda: None
        poller.start()
        poller.stop()
        assert poller._stop.is_set()

    # ── checkpoint logic ──────────────────────────────────

    def test_stale_items_before_checkpoint_skipped(self):
        """Items with updated_at older than checkpoint should be skipped."""
        poller = self._make_poller(["org/repo"])
        future_checkpoint = "2099-01-01T00:00:00Z"

        old_items = [
            {"number": 1, "updated_at": "2020-01-01T00:00:00Z", "state": "open", "user": {}},
        ]
        fake_urllib = _FakeUrlOpen({
            "org/repo/pulls": old_items,
            "org/repo/issues": old_items,
        })

        poller._poll_repo("org/repo", future_checkpoint, urllib_request=fake_urllib, json=json)

        # Nothing published because all items are older than checkpoint
        assert poller._event_bus.consume(timeout=0.1) is None

    def test_new_items_after_checkpoint_are_published(self):
        poller = self._make_poller(["org/repo"])

        new_pr = [
            {"number": 5, "updated_at": "2026-06-01T00:00:00Z", "state": "open", "user": {"login": "dev"}},
        ]
        new_issue = [
            {"number": 10, "updated_at": "2026-06-02T00:00:00Z", "state": "open", "user": {"login": "qa"}},
        ]
        fake_urllib = _FakeUrlOpen({
            "org/repo/pulls": new_pr,
            "org/repo/issues": new_issue,
        })

        poller._poll_repo("org/repo", "2020-01-01T00:00:00Z", urllib_request=fake_urllib, json=json)

        # Two events published (one PR, one issue)
        e1 = poller._event_bus.consume(timeout=1)
        e2 = poller._event_bus.consume(timeout=1)
        assert e1 is not None
        assert e2 is not None
        types = {e1.type, e2.type}
        assert types == {"github_pr", "github_issue"}

    def test_checkpoint_advances_to_newest(self):
        poller = self._make_poller(["org/repo"])

        items = [
            {"number": 1, "updated_at": "2026-03-01T00:00:00Z", "state": "open", "user": {}},
            {"number": 2, "updated_at": "2026-03-05T00:00:00Z", "state": "open", "user": {}},
        ]
        fake_urllib = _FakeUrlOpen({
            "org/repo/pulls": items,
            "org/repo/issues": [],
        })

        poller._poll_repo("org/repo", "2020-01-01T00:00:00Z", urllib_request=fake_urllib, json=json)

        assert poller._last_polled["org/repo"] == "2026-03-05T00:00:00Z"

    def test_stale_items_at_checkpoint_boundary_skipped(self):
        """Items with updated_at == checkpoint should be skipped."""
        poller = self._make_poller(["org/repo"])
        checkpoint = "2026-01-15T00:00:00Z"

        items = [
            {"number": 1, "updated_at": checkpoint, "state": "open", "user": {}},
        ]
        fake_urllib = _FakeUrlOpen({
            "org/repo/pulls": items,
            "org/repo/issues": [],
        })

        poller._poll_repo("org/repo", checkpoint, urllib_request=fake_urllib, json=json)

        assert poller._event_bus.consume(timeout=0.1) is None

    # ── issue filtering ───────────────────────────────────

    def test_pulls_in_issues_endpoint_are_filtered(self):
        """GitHub issues endpoint includes PRs; poller must skip those."""
        poller = self._make_poller(["org/repo"])

        mixed_issues = [
            {"number": 1, "updated_at": "2026-06-01T00:00:00Z", "state": "open", "user": {}},  # plain issue
            {"number": 2, "updated_at": "2026-06-01T00:00:00Z", "state": "open", "user": {}, "pull_request": {"url": "..."}},  # should be skipped
        ]
        fake_urllib = _FakeUrlOpen({
            "org/repo/pulls": [],
            "org/repo/issues": mixed_issues,
        })

        poller._poll_repo("org/repo", "2020-01-01T00:00:00Z", urllib_request=fake_urllib, json=json)

        event = poller._event_bus.consume(timeout=1)
        assert event is not None
        assert event.type == "github_issue"
        assert event.payload["issue"]["number"] == 1
        # No second event
        assert poller._event_bus.consume(timeout=0.1) is None

    # ── error handling ────────────────────────────────────

    def test_http_error_does_not_crash(self):
        """Network errors in one repo/kind should not affect others."""

        class FailingUrlLib:
            Request = _FakeUrlOpen({}).Request

            def urlopen(self, req, timeout=15):
                raise OSError("connection refused")

        poller = self._make_poller(["org/repo"])
        poller._poll_repo("org/repo", "2020-01-01T00:00:00Z", urllib_request=FailingUrlLib(), json=json)

    def test_non_list_response_skipped(self):
        poller = self._make_poller(["org/repo"])
        fake_urllib = _FakeUrlOpen({
            "org/repo/pulls": {"error": "rate limited"},  # not a list
            "org/repo/issues": [],
        })
        poller._poll_repo("org/repo", "2020-01-01T00:00:00Z", urllib_request=fake_urllib, json=json)
        assert poller._event_bus.consume(timeout=0.1) is None

    # ── multi-repo ────────────────────────────────────────

    def test_multiple_repos_each_polled(self):
        poller = self._make_poller(["org/a", "org/b"])

        items = [{"number": 1, "updated_at": "2026-06-01T00:00:00Z", "state": "open", "user": {}}]
        fake_urllib = _FakeUrlOpen({
            "org/a/pulls": items,
            "org/a/issues": [],
            "org/b/pulls": items,
            "org/b/issues": [],
        })

        poller._poll_repo("org/a", "2020-01-01T00:00:00Z", urllib_request=fake_urllib, json=json)
        poller._poll_repo("org/b", "2020-01-01T00:00:00Z", urllib_request=fake_urllib, json=json)

        # Two events: one from each repo
        e1 = poller._event_bus.consume(timeout=1)
        e2 = poller._event_bus.consume(timeout=1)
        assert e1 is not None
        assert e2 is not None
        assert poller._event_bus.consume(timeout=0.1) is None
