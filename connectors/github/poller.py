"""Background thread that polls the GitHub REST API for PR and issue updates."""

from datetime import datetime, timezone
import logging
import threading
import time

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
        # Initialize checkpoints to "now" so we skip stale items on first poll
        now = datetime.now(timezone.utc).isoformat()
        for repo in self._repos:
            self._last_polled.setdefault(repo, now)

        while not self._stop.wait(self._interval_sec):
            try:
                self._poll_once()
            except Exception:
                logger.exception("github poller error")

    def _poll_once(self) -> None:
        import urllib.request
        import json as _json

        for repo in self._repos:
            checkpoint = self._last_polled.get(repo, "")
            self._poll_repo(repo, checkpoint, urllib_request=urllib.request, json=_json)

    def _poll_repo(self, repo: str, checkpoint: str, *, urllib_request, json) -> None:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "EVA-AI/0.1",
        }
        newest = checkpoint

        for kind in ("pulls", "issues"):
            url = (
                f"https://api.github.com/repos/{repo}/{kind}"
                f"?state=open&sort=updated&per_page=10"
            )
            try:
                req = urllib_request.Request(url, headers=headers)
                with urllib_request.urlopen(req, timeout=15) as resp:
                    items = json.loads(resp.read())
            except Exception:
                logger.warning("github poll failed for %s/%s", repo, kind)
                continue

            if not isinstance(items, list):
                continue

            for item in items:
                updated = item.get("updated_at", "")
                if updated <= checkpoint:
                    continue
                if updated > newest:
                    newest = updated

                # Issues endpoint includes PRs; skip those
                if kind == "issues" and "pull_request" in item:
                    continue

                try:
                    from connectors.github.event_normalizer import normalize_poller_payload

                    event_kind = "pr" if kind == "pulls" else "issue"
                    event = normalize_poller_payload(event_kind, repo, item)
                    self._event_bus.publish(event)
                    logger.debug("poller published %s %s/%s", event_kind, repo, item.get("number"))
                except Exception:
                    logger.exception("failed to normalize polled item")

        self._last_polled[repo] = newest
