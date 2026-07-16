"""WebSocket manager unit tests."""


class TestWebSocketManager:
    def test_init_state(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        stats = mgr.stats()
        assert stats["total_connections"] == 0
        assert stats["messages_sent"] == 0
        assert len(stats["channels"]) == 4

    def test_broadcast_sync_no_clients(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        # should not raise — no clients to push to
        mgr.broadcast_sync("entity_created", {"type": "task", "name": "test"})

    def test_broadcast_sync_policy_state(self):
        from runtime.websocket import WebSocketManager
        mgr = WebSocketManager()
        mgr.broadcast_sync("policy_state", {"state_machine": {"current": "dormant"}})
        # no clients, should not raise

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
        # silently no-ops

    def test_ws_stats_endpoint(self, client):
        """GET /health/ws returns connection stats."""
        response = client.get("/health/ws")
        assert response.status_code == 200
        data = response.json()
        assert "channels" in data
        assert "total_connections" in data
        assert data["total_connections"] == 0
