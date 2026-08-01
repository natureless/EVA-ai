"""Playwright Browser Executor — real browser automation for EVA.

Provides headless browser capabilities:
- navigate: open a URL and get page content
- click: click an element by selector
- fill: fill a form field
- extract: extract text/content from elements
- screenshot: capture page screenshot (returns base64)

Requires: pip install playwright && playwright install chromium

Security:
- URL whitelist enforcement (same as existing BrowserExecutor)
- Private/loopback IP blocking (SSRF prevention)
- Configurable timeout and page size limits
- Runs in headless mode by default
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from core.executor import (
    BaseExecutor,
    ExecutorAuditLog,
    ExecutorDecision,
    _is_private_or_loopback,
    _parse_timeout_sec,
    DEFAULT_BROWSER_TIMEOUT,
    DEFAULT_BROWSER_MAX_SIZE,
    DEFAULT_BROWSER_ALLOWED_SCHEMES,
)

logger = logging.getLogger("eva.executor.browser")

# ── Playwright import (lazy) ──────────────────────────────────

_playwright = None


def _get_playwright():
    """Lazy-import playwright to avoid hard dependency."""
    global _playwright
    if _playwright is None:
        try:
            from playwright.sync_api import sync_playwright
            _playwright = sync_playwright
        except ImportError:
            raise ImportError(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )
    return _playwright


# ── Actions ────────────────────────────────────────────────────

class PlaywrightBrowserExecutor(BaseExecutor):
    """Real browser automation via Playwright.

    Supports: navigate, click, fill, extract, screenshot.
    Falls back gracefully if Playwright is not installed.
    """

    name = "playwright_browser"
    description = "Headless browser automation (navigate, click, fill, extract, screenshot)"

    def __init__(
        self,
        audit_log: ExecutorAuditLog,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(audit_log, config)
        cfg = (config or {}).get("executors", {}).get("browser", {})
        limits = cfg.get("limits", {})
        self.timeout = _parse_timeout_sec(limits.get("timeout", DEFAULT_BROWSER_TIMEOUT))
        raw_max = limits.get("max_page_size_mb", 5)
        self.max_size = int(raw_max * 1024 * 1024) if isinstance(raw_max, (int, float)) else DEFAULT_BROWSER_MAX_SIZE
        self.allowed_domains: list[str] = cfg.get("allowed_domains", [])
        self._browser = None
        self._playwright_instance = None

    # ── Boundary check ──────────────────────────────────────

    def check_boundaries(self, params: dict[str, Any]) -> ExecutorDecision:
        url = params.get("url", "")
        if not url:
            return ExecutorDecision(allowed=False, reason="url is required")

        parsed = __import__("urllib.parse").urlparse(url) if "://" in url else None
        if not parsed or parsed.scheme not in DEFAULT_BROWSER_ALLOWED_SCHEMES:
            return ExecutorDecision(allowed=False, reason=f"only http/https allowed, got: {url[:80]}")

        hostname = parsed.hostname or ""
        if not hostname:
            return ExecutorDecision(allowed=False, reason="could not parse hostname from url")

        if _is_private_or_loopback(hostname):
            return ExecutorDecision(
                allowed=False,
                reason=f"private/loopback address blocked: {hostname}",
            )

        if self.allowed_domains:
            if not any(
                hostname == domain or hostname.endswith("." + domain)
                for domain in self.allowed_domains
            ):
                return ExecutorDecision(
                    allowed=False,
                    reason=f"domain {hostname} not in whitelist",
                )

        return ExecutorDecision(allowed=True, reason="boundary check passed")

    # ── Action dispatch ─────────────────────────────────────

    def _run(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch to the appropriate handler based on action."""
        handlers = {
            "navigate": self._handle_navigate,
            "click": self._handle_click,
            "fill": self._handle_fill,
            "extract": self._handle_extract,
            "screenshot": self._handle_screenshot,
        }

        handler = handlers.get(action)
        if handler is None:
            return {"ok": False, "error": f"unknown action: {action}", "summary": f"unknown action: {action}"}

        try:
            return handler(params)
        except ImportError as e:
            return {"ok": False, "error": str(e), "summary": "playwright not installed"}
        except Exception as e:
            logger.exception("Playwright action '%s' failed", action)
            return {"ok": False, "error": str(e), "summary": f"{action} failed: {e}"}

    # ── Action handlers ─────────────────────────────────────

    def _handle_navigate(self, params: dict[str, Any]) -> dict[str, Any]:
        """Navigate to a URL and return page text content."""
        url = params["url"]
        wait_until = params.get("wait_until", "domcontentloaded")

        pw = _get_playwright()()
        try:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=self.timeout * 1000, wait_until=wait_until)

            title = page.title()
            text = page.inner_text("body")[:self.max_size]
            html = page.content()[:self.max_size]

            browser.close()
            return {
                "ok": True,
                "url": url,
                "title": title,
                "text": text,
                "html": html,
                "text_length": len(text),
                "summary": f"Navigated to {url[:100]} — title: {title[:80]}, {len(text)} chars",
            }
        finally:
            pw.stop()

    def _handle_click(self, params: dict[str, Any]) -> dict[str, Any]:
        """Click an element and return resulting page state."""
        url = params["url"]
        selector = params.get("selector", "")

        if not selector:
            return {"ok": False, "error": "selector is required for click action"}

        pw = _get_playwright()()
        try:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")

            page.click(selector, timeout=10000)
            page.wait_for_load_state("networkidle", timeout=15000)

            title = page.title()
            text = page.inner_text("body")[:self.max_size]

            browser.close()
            return {
                "ok": True,
                "url": page.url,  # may have changed after click
                "title": title,
                "text": text,
                "text_length": len(text),
                "summary": f"Clicked '{selector}' on {url[:80]} — landed on {page.url[:80]}",
            }
        finally:
            pw.stop()

    def _handle_fill(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fill a form field and return page state."""
        url = params["url"]
        selector = params.get("selector", "")
        value = params.get("value", "")

        if not selector:
            return {"ok": False, "error": "selector is required for fill action"}

        pw = _get_playwright()()
        try:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")

            page.fill(selector, value)

            # Return the page state after filling
            text = page.inner_text("body")[:self.max_size]

            browser.close()
            return {
                "ok": True,
                "url": url,
                "selector": selector,
                "value": value,
                "text": text,
                "summary": f"Filled '{selector}' with '{value[:50]}' on {url[:80]}",
            }
        finally:
            pw.stop()

    def _handle_extract(self, params: dict[str, Any]) -> dict[str, Any]:
        """Extract text from specific elements on a page."""
        url = params["url"]
        selector = params.get("selector", "body")

        pw = _get_playwright()()
        try:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")

            elements = page.query_selector_all(selector)
            texts = [el.inner_text() for el in elements[:20]]  # max 20 elements
            combined = "\n---\n".join(texts)[:self.max_size]

            browser.close()
            return {
                "ok": True,
                "url": url,
                "selector": selector,
                "elements_found": len(elements),
                "text": combined,
                "text_length": len(combined),
                "summary": f"Extracted {len(elements)} elements matching '{selector}' from {url[:80]}",
            }
        finally:
            pw.stop()

    def _handle_screenshot(self, params: dict[str, Any]) -> dict[str, Any]:
        """Take a screenshot of a page, return as base64."""
        url = params["url"]
        full_page = params.get("full_page", False)

        pw = _get_playwright()()
        try:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")

            screenshot_bytes = page.screenshot(full_page=full_page, type="jpeg", quality=80)
            b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            browser.close()
            return {
                "ok": True,
                "url": url,
                "screenshot_base64": b64,
                "size_bytes": len(screenshot_bytes),
                "summary": f"Screenshot of {url[:80]} — {len(screenshot_bytes)} bytes",
            }
        finally:
            pw.stop()


# ── Availability check ─────────────────────────────────────────

def is_playwright_available() -> bool:
    """Check if Playwright is installed and usable."""
    try:
        _get_playwright()
        return True
    except ImportError:
        return False
