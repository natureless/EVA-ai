"""WebSocket real-time event streaming.

Broadcasts system events to connected dashboard clients:
- policy_state   — state machine transitions, token counts
- entity_created — new world model entities
- audit_event    — executor audit log entries
- system_state   — periodic system health snapshot
- chat_reply     — final agent response (sync mode)
- chat_token     — per-token streaming agent output

Usage in cognition loop::

    ws = container["ws_manager"]
    ws.broadcast_sync("policy_state", policy_engine.get_state())
    ws.broadcast_sync("chat_token", {"task_id": tid, "token": t})
"""

import asyncio
import json
import logging
import time
from typing import Any, Callable, Coroutine

from fastapi import WebSocket

logger = logging.getLogger("eva.websocket")

ChannelCallback = Callable[[str, Any], Coroutine[None, None, None]]


class WebSocketManager:
    """Manages connected WebSocket clients and callback subscribers."""

    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = {
            "policy_state": set(),
            "entity_created": set(),
            "audit_event": set(),
            "system_state": set(),
            "chat_reply": set(),
            "chat_token": set(),
        }
        self._message_count = 0
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._callbacks: dict[str, list[ChannelCallback]] = {}

    # ── lifecycle ────────────────────────────────────────────

    def capture_loop(self) -> None:
        """Store the current event loop for cross-thread broadcasts.

        Must be called from the main async thread during startup.
        """
        self._main_loop = asyncio.get_running_loop()

    # ── connection management ────────────────────────────────

    async def connect(self, websocket: WebSocket, channel: str = "") -> None:
        """Accept a WS connection and subscribe it to a channel."""
        await websocket.accept()
        if channel in self._clients:
            self._clients[channel].add(websocket)
        else:
            # subscribe to all channels
            for ch in self._clients:
                self._clients[ch].add(websocket)
        logger.debug("ws client connected channel=%s (total=%d)",
                     channel or "all", self._count_all())

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a disconnected client from all channels."""
        for ch in self._clients:
            self._clients[ch].discard(websocket)
        logger.debug("ws client disconnected (total=%d)", self._count_all())

    # ── callbacks (for in-process subscribers like SSE) ──────

    def subscribe(self, channel: str, callback: ChannelCallback) -> None:
        """Register an async callback for a channel.

        Callbacks receive ``(channel, payload)`` and are invoked after
        WebSocket clients are broadcast to.
        """
        self._callbacks.setdefault(channel, []).append(callback)

    def unsubscribe(self, channel: str, callback: ChannelCallback) -> None:
        """Remove a previously registered callback."""
        cb_list = self._callbacks.get(channel)
        if cb_list:
            try:
                cb_list.remove(callback)
            except ValueError:
                pass

    # ── broadcast ────────────────────────────────────────────

    async def broadcast(self, channel: str, payload: Any) -> int:
        """Push payload to all WS clients and callback subscribers."""
        data = json.dumps({
            "channel": channel,
            "payload": payload,
            "ts": time.time(),
        }, ensure_ascii=False, default=str)

        dead: list[WebSocket] = []
        sent = 0
        for ws in self._clients.get(channel, set()):
            try:
                await ws.send_text(data)
                sent += 1
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

        # notify callback subscribers
        for cb in self._callbacks.get(channel, []):
            try:
                await cb(channel, payload)
            except Exception:
                logger.debug("callback error channel=%s", channel, exc_info=True)

        self._message_count += sent
        return sent

    def broadcast_sync(self, channel: str, payload: Any) -> None:
        """Thread-safe fire-and-forget broadcast.

        Safe to call from any thread. Uses ``call_soon_threadsafe`` to
        schedule the real broadcast on the main event loop.
        """
        if self._main_loop and self._main_loop.is_running():
            self._main_loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self.broadcast(channel, payload))
            )

    # ── stats ────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "channels": {ch: len(s) for ch, s in self._clients.items()},
            "total_connections": self._count_all(),
            "messages_sent": self._message_count,
        }

    def _count_all(self) -> int:
        seen: set[int] = set()
        for s in self._clients.values():
            for ws in s:
                seen.add(id(ws))
        return len(seen)
