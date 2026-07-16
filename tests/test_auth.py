"""Authentication middleware tests."""

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from runtime.auth import AuthMiddleware


async def _ok_endpoint(request: Request):
    return JSONResponse({"ok": True})


def _build_app(token: str = ""):
    app = Starlette()
    app.add_middleware(AuthMiddleware, token=token)

    @app.route("/health/live")
    async def health(request):
        return JSONResponse({"status": "alive"})

    @app.route("/api/chat")
    async def chat(request):
        return JSONResponse({"reply": "hello"})

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

    def test_query_param_token_passes(self):
        client = TestClient(_build_app(token="secret"))
        resp = client.get("/api/chat?token=secret")
        assert resp.status_code == 200

    def test_health_still_open_with_token(self):
        client = TestClient(_build_app(token="secret"))
        resp = client.get("/health/live")
        assert resp.status_code == 200

    def test_metrics_open(self):
        client = TestClient(_build_app(token="secret"))

        @client.app.route("/metrics")
        async def metrics(request):
            return JSONResponse({"requests": 0})

        resp = client.get("/metrics")
        assert resp.status_code == 200
