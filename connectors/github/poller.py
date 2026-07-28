"""Background thread that polls the GitHub REST API for PR and issue updates."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
from typing import Any

from event.event_bus import EventBus

logger = logging.getLogger(__name__)


class GitHubPoller:
    def __init__(
        self,
        event_bus: EventBus,
        token: str,
        repos: list[str],
        interval_sec: int = 300,
    ) -> None:
        self._event_bus = event_bus
        self._token = token
        self._repos = repos
        self._interval_sec = interval_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Per-repo checkpoint: ISO timestamp of last seen updated_at
        self._last_polled: dict[str, str] = {}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_forever, daemon=True, name="eva-github-poller"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _run_forever(self) -> None:
        """Main poller loop: init checkpoints once, then poll each interval."""
        self._init_poll_checkpoints()
        while not self._stop.wait(self._interval_sec):
            try:
                self._poll_once()
            except Exception:
                logger.exception("github poller error")

    # ── private: checkpoint lifecycle ──────────────────────

    def _init_poll_checkpoints(self) -> None:
        """Seed per-repo checkpoints to 'now' so stale items are skipped on first poll."""
        now = datetime.now(timezone.utc).isoformat()
        for repo in self._repos:
            self._last_polled.setdefault(repo, now)

    def _advance_checkpoint(self, repo: str, items: list[dict[str, Any]], current: str) -> str:
        """Return the newest updated_at across items, clamped to ≥ current."""
        newest = current
        for item in items:
            updated = item.get("updated_at", "")
            if updated > newest:
                newest = updated
        return newest

    # ── private: HTTP helpers ──────────────────────────────

    def _github_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "EVA-AI/0.1",
        }

    def _fetch_repo_items(
        self, repo: str, kind: str, *, urllib_request: Any, json: Any
    ) -> list[dict[str, Any]] | None:
        """Fetch a single page of items from GitHub. Returns None on failure."""
        url = (
            f"https://api.github.com/repos/{repo}/{kind}"
            f"?state=open&sort=updated&per_page=10"
        )
        try:
            req = urllib_request.Request(url, headers=self._github_headers())
            with urllib_request.urlopen(req, timeout=15) as resp:
                items = json.loads(resp.read())
            if not isinstance(items, list):
                return None
            return items
        except Exception:
            logger.warning("github poll failed for %s/%s", repo, kind)
            return None

    # ── private: item filtering & publishing ───────────────

    def _is_publishable(self, item: dict[str, Any], checkpoint: str, kind: str) -> bool:
        """True if item should be published (after checkpoint, not a PR in issues)."""
        updated = item.get("updated_at", "")
        if updated <= checkpoint:
            return False
        if kind == "issues" and "pull_request" in item:
            return False
        return True

    def _publish_poller_item(self, item: dict[str, Any], kind: str, repo: str) -> None:
        """Normalize and publish a single polled item to the event bus."""
        from connectors.github.event_normalizer import normalize_poller_payload

        event_kind = "pr" if kind == "pulls" else "issue"
        event = normalize_poller_payload(event_kind, repo, item)
        self._event_bus.publish(event)
        logger.debug("poller published %s %s/%s", event_kind, repo, item.get("number"))

    def _poll_once(self) -> None:
        import urllib.request
        import json as _json

        for repo in self._repos:
            checkpoint = self._last_polled.get(repo, "")
            self._poll_repo(repo, checkpoint, urllib_request=urllib.request, json=_json)

    def _poll_repo(self, repo: str, checkpoint: str, *, urllib_request: Any, json: Any) -> None:
        """Poll both pulls and issues for a single repo, advancing the checkpoint."""
        newest = checkpoint

        for kind in ("pulls", "issues"):
            items = self._fetch_repo_items(repo, kind, urllib_request=urllib_request, json=json)
            if items is None:
                continue

            newest = self._advance_checkpoint(repo, items, newest)

            for item in items:
                if not self._is_publishable(item, checkpoint, kind):
                    continue
                try:
                    self._publish_poller_item(item, kind, repo)
                except Exception:
                    logger.exception("failed to normalize polled item")

        self._last_polled[repo] = newest
