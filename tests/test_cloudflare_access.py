"""Exercise the public boundary with real signatures and HTTP/stream/WS clients."""
from dataclasses import replace
from types import SimpleNamespace
import time

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from jwt.exceptions import PyJWKClientConnectionError
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from runtime.auth import AuthMiddleware
from runtime.cloudflare_access import (
    AccessConfig, AccessDenied, AccessUnavailable, AccessVerifier, CloudflareAccessMiddleware,
)

CONFIG = AccessConfig("https://eva-test.cloudflareaccess.com", "test-audience",
                      frozenset({"owner@example.com"}), "https://eva.example.com")
API_KEY = "test-local-api-key"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def signed(key, **overrides):
    claims = {"iss": CONFIG.team_domain, "aud": [CONFIG.audience], "sub": "user-1",
              "email": "owner@example.com", "iat": int(time.time()) - 5, "exp": int(time.time()) + 300}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


class Keys:
    def __init__(self, key):
        self.key = key.public_key()
        self.lookups = 0

    def get_signing_key_from_jwt(self, _token):
        self.lookups += 1
        return SimpleNamespace(key=self.key)


def verifier(key):
    return AccessVerifier(CONFIG, jwks_client=Keys(key))


def build_app(key, *, verify=None, enabled=True):
    app = Starlette()

    async def view(request):
        assert request.headers["x-api-token"] == API_KEY
        return JSONResponse({"ok": True, "email": getattr(request.state, "access_identity", {}).get("email")})

    async def stream(request):
        assert request.headers["x-api-token"] == API_KEY

        async def chunks():
            yield "data: first\n\n"
            yield "data: second\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    async def socket(websocket):
        assert websocket.headers["x-api-token"] == API_KEY
        await websocket.accept()
        await websocket.send_text("connected")
        await websocket.close()

    for path in ["/", "/api/memory/graph", "/health/live", "/docs", "/static/test.js"]:
        app.add_route(path, view, methods=["GET", "POST"])
    app.add_route("/stream", stream)
    app.add_websocket_route("/ws", socket)
    app.add_middleware(AuthMiddleware, token=API_KEY)
    app.add_middleware(CloudflareAccessMiddleware, config=CONFIG if enabled else None,
                       api_token=API_KEY, verifier=verify or verifier(key))
    return app


def test_config_is_optional_but_partial_config_fails_closed():
    assert AccessConfig.from_env({}) is None
    with pytest.raises(ValueError, match="partially"):
        AccessConfig.from_env({"EVA_PUBLIC_ORIGIN": CONFIG.public_origin})
    with pytest.raises(ValueError, match="EVA_API_TOKEN"):
        CloudflareAccessMiddleware(None, config=CONFIG)


def test_production_entry_validates_before_loading_app(tmp_path, monkeypatch):
    from app import cloudflare_server

    env_file = tmp_path / 'deployment.env'
    env_file.write_text('')
    monkeypatch.setattr(cloudflare_server, 'load_dotenv', lambda *a, **k: None)
    monkeypatch.setattr(cloudflare_server.os, 'environ', {})
    with pytest.raises(ValueError, match='must be configured'):
        cloudflare_server.configure(env_file)
    cloudflare_server.os.environ.update({
        'EVA_CF_ACCESS_TEAM_DOMAIN': CONFIG.team_domain, 'EVA_CF_ACCESS_AUD': CONFIG.audience,
        'EVA_CF_ACCESS_ALLOWED_EMAILS': 'owner@example.com', 'EVA_PUBLIC_ORIGIN': CONFIG.public_origin,
    })
    with pytest.raises(ValueError, match='at least 32'):
        cloudflare_server.configure(env_file)
    cloudflare_server.os.environ['EVA_API_TOKEN'] = 'x' * 32
    assert cloudflare_server.configure(env_file) == CONFIG
    assert cloudflare_server.os.environ['EVA_HOST'] == '127.0.0.1'
    assert cloudflare_server.os.environ['EVA_ENV'] == 'production'


@pytest.mark.parametrize("change", [
    {"team_domain": "https://evil.example.com"},
    {"team_domain": "http://eva-test.cloudflareaccess.com"},
    {"public_origin": "https://eva.example.com/path"},
    {"public_origin": "https://user@eva.example.com"},
    {"allowed_emails": frozenset({"*@example.com"})},
    {"allowed_emails": frozenset()},
])
def test_config_rejects_unsafe_boundary(change):
    with pytest.raises(ValueError):
        replace(CONFIG, **change)


def test_real_signature_and_exact_identity(signing_key):
    assert verifier(signing_key).verify(signed(signing_key, email="OWNER@example.com")) == {
        "email": "owner@example.com", "subject": "user-1"}


@pytest.mark.parametrize("change", [
    {"iss": "https://other.cloudflareaccess.com"}, {"aud": ["other-app"]},
    {"email": "intruder@example.com"}, {"email": "owner@example.com.evil"},
    {"email": None}, {"sub": None}, {"sub": ""}, {"exp": None}, {"iat": None},
    {"exp": 1}, {"iat": 4_000_000_000}, {"nbf": 4_000_000_000},
])
def test_claims_are_verified(signing_key, change):
    with pytest.raises(AccessDenied):
        verifier(signing_key).verify(signed(signing_key, **change))


def test_wrong_signature_is_rejected(signing_key):
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AccessDenied):
        verifier(signing_key).verify(signed(wrong_key))


@pytest.mark.parametrize("token", ["not-a-jwt", "a" * 16385,
    jwt.encode({"email": "owner@example.com"}, "a" * 32, algorithm="HS256", headers={"kid": "evil"}),
    jwt.encode({"email": "owner@example.com"}, None, algorithm="none")])
def test_untrusted_algorithm_never_fetches_keys(signing_key, token):
    check = verifier(signing_key)
    with pytest.raises(AccessDenied):
        check.verify(token)
    assert check.jwks.lookups == 0


def test_key_network_failure_is_not_authentication(signing_key):
    class OfflineKeys:
        def get_signing_key_from_jwt(self, _token):
            raise PyJWKClientConnectionError("offline")

    check = AccessVerifier(CONFIG, jwks_client=OfflineKeys())
    with pytest.raises(AccessUnavailable):
        check.verify(signed(signing_key))
    client = TestClient(build_app(signing_key, verify=check))
    assert client.get("/", headers={"Cf-Access-Jwt-Assertion": signed(signing_key)}).status_code == 503


@pytest.mark.parametrize("path", ["/", "/api/memory/graph", "/health/live", "/docs", "/static/test.js"])
def test_no_public_exceptions_when_access_enabled(signing_key, path):
    client = TestClient(build_app(signing_key))
    response = client.get(path, headers={"Cf-Access-Authenticated-User-Email": "owner@example.com"})
    assert response.status_code == 401
    assert "no-store" in response.headers["cache-control"]
    assert client.get(path + "?token=" + API_KEY).status_code == 401


def test_verified_access_injects_key_internally_and_disables_caching(signing_key):
    client = TestClient(build_app(signing_key))
    response = client.get("/", headers={"Cf-Access-Jwt-Assertion": signed(signing_key), "X-API-Token": "forged"})
    assert response.status_code == 200
    assert response.json()["email"] == "owner@example.com"
    assert API_KEY not in response.text and API_KEY not in str(response.headers)
    for header in ["cache-control", "cdn-cache-control", "cloudflare-cdn-cache-control"]:
        assert "no-store" in response.headers[header]
    assert response.headers["referrer-policy"] == "no-referrer"


def test_program_api_key_remains_compatible(signing_key):
    client = TestClient(build_app(signing_key))
    assert client.post("/", headers={"X-API-Token": API_KEY}).status_code == 200
    assert client.get("/", headers={"X-API-Token": "wrong"}).status_code == 401
    assert client.get("/", headers={b"X-API-Token": b"\xff"}).status_code == 401
    local = TestClient(build_app(signing_key, enabled=False))
    assert local.get("/", headers={"X-API-Token": API_KEY}).status_code == 200


def test_browser_write_origin_must_match(signing_key):
    client = TestClient(build_app(signing_key))
    headers = {"Cf-Access-Jwt-Assertion": signed(signing_key), "Origin": "https://evil.example.com"}
    assert client.post("/", headers=headers).status_code == 403
    headers["Origin"] = CONFIG.public_origin
    assert client.post("/", headers=headers).status_code == 200


def test_sse_and_websocket_work_without_frontend_api_key(signing_key):
    client = TestClient(build_app(signing_key))
    headers = {"Cf-Access-Jwt-Assertion": signed(signing_key), "Origin": CONFIG.public_origin}
    with client.stream("GET", "/stream", headers=headers) as response:
        assert response.status_code == 200
        assert list(response.iter_lines()) == ["data: first", "", "data: second", ""]
    with client.websocket_connect("/ws", headers=headers) as socket:
        assert socket.receive_text() == "connected"


@pytest.mark.parametrize("kind,code", [("missing", 4401), ("forged", 4403), ("origin", 4403)])
def test_websocket_cannot_skip_access(signing_key, kind, code):
    client = TestClient(build_app(signing_key))
    headers = {} if kind == "missing" else {"Cf-Access-Jwt-Assertion": "forged" if kind == "forged" else signed(signing_key)}
    if kind == "origin":
        headers["Origin"] = "https://evil.example.com"
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws?token=" + API_KEY, headers=headers):
            pass
    assert closed.value.code == code
