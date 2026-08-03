"""Authentication middleware tests."""

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from runtime.auth import AuthMiddleware

def _build_app(token: str = ""):
    app = Starlette()
    app.add_middleware(AuthMiddleware, token=token)

    async def health(_request):
        return JSONResponse({"status": "alive"})

    async def chat(_request):
        return JSONResponse({"reply": "hello"})

    async def code_execute(_request):
        return JSONResponse({"ok": True})

    async def file_write(_request):
        return JSONResponse({"ok": True})

    async def tool_call(_request):
        return JSONResponse({"ok": True})

    async def health_recover(_request):
        return JSONResponse({"ok": True})

    app.add_route("/health/live", health)
    app.add_route("/api/chat", chat)
    app.add_route("/api/executors/code/execute", code_execute, methods=["POST"])
    app.add_route("/api/executors/file/write", file_write, methods=["POST"])
    app.add_route("/api/tools/call", tool_call, methods=["POST"])
    app.add_route("/health/recover", health_recover, methods=["POST"])
    return app


class TestAuthMiddleware:
    def test_health_open_without_token(self):
        client = TestClient(_build_app(token=""))
        resp = client.get("/health/live")
        assert resp.status_code == 200

    def test_api_open_without_token(self):
        """Without a configured token, all routes pass through."""
        client = TestClient(_build_app(token=""))
        resp = client.get("/api/chat")
        assert resp.status_code == 200

    def test_api_blocked_with_token(self):
        client = TestClient(_build_app(token="secret"))
        resp = client.get("/api/chat")
        assert resp.status_code == 401

    def test_wrong_token_rejected(self):
        client = TestClient(_build_app(token="secret"))
        resp = client.get("/api/chat", headers={"X-API-Token": "wrong"})
        assert resp.status_code == 403

    def test_correct_header_passes(self):
        client = TestClient(_build_app(token="secret"))
        resp = client.get("/api/chat", headers={"X-API-Token": "secret"})
        assert resp.status_code == 200

    def test_query_param_token_is_rejected(self):
        client = TestClient(_build_app(token="secret"))
        resp = client.get("/api/chat?token=secret")
        assert resp.status_code == 401

    def test_health_still_open_with_token(self):
        client = TestClient(_build_app(token="secret"))
        resp = client.get("/health/live")
        assert resp.status_code == 200

    def test_metrics_open(self):
        client = TestClient(_build_app(token="secret"))

        async def metrics(_request):
            return JSONResponse({"requests": 0})

        client.app.add_route("/metrics", metrics)
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_dev_mode_blocks_code_execute(self):
        """Write/execute endpoints require auth even without a configured token."""
        client = TestClient(_build_app(token=""))
        resp = client.post("/api/executors/code/execute")
        assert resp.status_code == 401

    def test_dev_mode_blocks_file_write(self):
        client = TestClient(_build_app(token=""))
        resp = client.post("/api/executors/file/write")
        assert resp.status_code == 401

    def test_dev_mode_blocks_direct_tool_call(self):
        client = TestClient(_build_app(token=""))
        resp = client.post("/api/tools/call")
        assert resp.status_code == 401

    def test_health_recovery_is_not_public(self):
        client = TestClient(_build_app(token=""))
        resp = client.post("/health/recover")
        assert resp.status_code == 401

    def test_dev_mode_allows_read_endpoint(self):
        """Read-only endpoints still pass through in dev mode."""
        client = TestClient(_build_app(token=""))
        resp = client.get("/api/chat")
        assert resp.status_code == 200

    def test_token_passes_write_endpoint(self):
        """With a valid token, write endpoints are allowed."""
        client = TestClient(_build_app(token="secret"))
        resp = client.post("/api/executors/code/execute", headers={"X-API-Token": "secret"})
        assert resp.status_code == 200

    def test_no_token_blocks_write_even_when_configured(self):
        """When a token is configured, missing it blocks write endpoints."""
        client = TestClient(_build_app(token="secret"))
        resp = client.post("/api/executors/code/execute")
        assert resp.status_code == 401
