"""WebSocket manager unit tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock


class TestWebSocketManager:
    def test_init_state(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        stats = mgr.stats()
        assert stats["total_connections"] == 0
        assert stats["messages_sent"] == 0
        assert len(stats["channels"]) == 6

    def test_broadcast_sync_no_clients(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        mgr.broadcast_sync("entity_created", {"type": "task", "name": "test"})

    def test_broadcast_sync_policy_state(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        mgr.broadcast_sync("policy_state", {"state_machine": {"current": "dormant"}})

    def test_broadcast_sync_audit_event(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        mgr.broadcast_sync("audit_event", {
            "executor_type": "file", "action": "read", "status": "success",
        })

    def test_broadcast_unknown_channel_silent(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        mgr.broadcast_sync("nonexistent_channel", {"test": True})

    def test_ws_stats_endpoint(self, client):
        response = client.get("/health/ws")
        assert response.status_code == 200
        data = response.json()
        assert "channels" in data
        assert "total_connections" in data
        assert data["total_connections"] == 0


class TestWebSocketAsync:
    """Async tests for connect, disconnect, broadcast with mock WebSockets."""

    def test_connect_to_specific_channel(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws = MagicMock()
        ws.accept = AsyncMock()

        async def _run():
            await mgr.connect(ws, channel="policy_state")

        asyncio.run(_run())
        assert ws in mgr._clients["policy_state"]
        assert ws not in mgr._clients["chat_reply"]

    def test_connect_empty_channel_subscribes_all(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws = MagicMock()
        ws.accept = AsyncMock()

        async def _run():
            await mgr.connect(ws, channel="")

        asyncio.run(_run())
        for ch_set in mgr._clients.values():
            assert ws in ch_set

    def test_disconnect_removes_from_all_channels(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws = MagicMock()
        ws.accept = AsyncMock()

        async def _run():
            await mgr.connect(ws, channel="")
            mgr.disconnect(ws)

        asyncio.run(_run())
        for ch_set in mgr._clients.values():
            assert ws not in ch_set

    def test_broadcast_sends_to_subscribed_client(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()

        async def _run():
            await mgr.connect(ws, channel="policy_state")
            sent = await mgr.broadcast("policy_state", {"state": "normal"})
            assert sent == 1
            ws.send_text.assert_called_once()

        asyncio.run(_run())

    def test_broadcast_skips_unsubscribed_channel(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()

        async def _run():
            await mgr.connect(ws, channel="policy_state")
            sent = await mgr.broadcast("chat_reply", {"reply": "hi"})
            assert sent == 0
            ws.send_text.assert_not_called()

        asyncio.run(_run())

    def test_broadcast_removes_dead_client(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock(side_effect=RuntimeError("disconnected"))

        async def _run():
            await mgr.connect(ws, channel="policy_state")
            sent = await mgr.broadcast("policy_state", {"state": "normal"})
            assert sent == 0
            assert ws not in mgr._clients["policy_state"]

        asyncio.run(_run())

    def test_broadcast_invokes_callbacks(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        callback = AsyncMock()

        async def _run():
            mgr.subscribe("policy_state", callback)
            await mgr.broadcast("policy_state", {"state": "quarantine"})
            callback.assert_called_once_with("policy_state", {"state": "quarantine"})

        asyncio.run(_run())

    def test_unsubscribe_removes_callback(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        cb1 = AsyncMock()
        cb2 = AsyncMock()

        async def _run():
            mgr.subscribe("policy_state", cb1)
            mgr.subscribe("policy_state", cb2)
            mgr.unsubscribe("policy_state", cb1)
            await mgr.broadcast("policy_state", {"state": "normal"})
            cb1.assert_not_called()
            cb2.assert_called_once()

        asyncio.run(_run())

    def test_unsubscribe_nonexistent_silent(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        cb = AsyncMock()
        mgr.unsubscribe("policy_state", cb)  # should not raise

    def test_broadcast_sync_schedules_on_running_loop(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        loop = MagicMock()
        loop.is_running.return_value = True
        mgr._main_loop = loop
        mgr.broadcast_sync("policy_state", {"state": "normal"})
        loop.call_soon_threadsafe.assert_called_once()

    def test_broadcast_sync_no_loop_does_nothing(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        mgr.broadcast_sync("policy_state", {"state": "normal"})

    def test_capture_loop_stores_running_loop(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()

        async def _run():
            mgr.capture_loop()
            assert mgr._main_loop is not None
            assert mgr._main_loop is asyncio.get_running_loop()

        asyncio.run(_run())

    def test_stats_with_connections(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws = MagicMock()
        ws.accept = AsyncMock()

        async def _run():
            await mgr.connect(ws, channel="policy_state")
            await mgr.connect(ws, channel="chat_reply")
            stats = mgr.stats()
            assert stats["total_connections"] == 1  # same WS, 2 channels
            assert stats["channels"]["policy_state"] == 1
            assert stats["channels"]["chat_reply"] == 1

        asyncio.run(_run())
