"""WebSocket real-time event streaming.

Broadcasts system events to connected dashboard clients:
- policy_state   — state machine transitions, token counts
- entity_created — new world model entities
- audit_event    — executor audit log entries
- system_state   — periodic system health snapshot

Usage in cognition loop::

    ws = container["ws_manager"]
    ws.broadcast("policy_state", policy_engine.get_state())
    ws.broadcast("entity_created", {"type": "task", "name": "Fix bug"})
"""

import json
import logging
import time
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("eva.websocket")


class WebSocketManager:
    """Manages connected WebSocket clients and channels."""

    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = {
            "policy_state": set(),
            "entity_created": set(),
            "audit_event": set(),
            "system_state": set(),
        }
        self._message_count = 0

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

    async def broadcast(self, channel: str, payload: Any) -> int:
        """Push payload to all clients subscribed to the channel."""
        if channel not in self._clients:
            return 0

        data = json.dumps({
            "channel": channel,
            "payload": payload,
            "ts": time.time(),
        }, ensure_ascii=False, default=str)

        dead: list[WebSocket] = []
        sent = 0
        for ws in self._clients[channel]:
            try:
                await ws.send_text(data)
                sent += 1
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

        self._message_count += sent
        return sent

    def broadcast_sync(self, channel: str, payload: Any) -> None:
        """Non-async fire-and-forget broadcast (for sync contexts)."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.broadcast(channel, payload))
        except RuntimeError:
            pass

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


# singleton
ws_manager = WebSocketManager()
