"""Optional Cloudflare Access boundary for both HTTP and WebSocket traffic."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import re
import secrets
from typing import Any, Mapping
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWTError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(frozen=True)
class AccessConfig:
    team_domain: str
    audience: str
    allowed_emails: frozenset[str]
    public_origin: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com", self.team_domain):
            raise ValueError("Cloudflare team domain must be an HTTPS cloudflareaccess.com team hostname")
        if not self.audience.strip() or not self.allowed_emails or any("@" not in email or "*" in email for email in self.allowed_emails):
            raise ValueError("Cloudflare audience and explicit allowed email addresses are required")
        origin = urlsplit(self.public_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment or any(c.isspace() for c in self.public_origin)):
            raise ValueError("EVA_PUBLIC_ORIGIN must be an HTTPS origin without path, query or credentials")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AccessConfig | None:
        env = os.environ if environ is None else environ
        values = [env.get(key, "").strip() for key in (
            "EVA_CF_ACCESS_TEAM_DOMAIN", "EVA_CF_ACCESS_AUD", "EVA_CF_ACCESS_ALLOWED_EMAILS", "EVA_PUBLIC_ORIGIN")]
        if not any(values):
            return None
        if not all(values):
            raise ValueError("Cloudflare Access is partially configured; all four settings are required")
        return cls(values[0].rstrip("/"), values[1],
                   frozenset(email.strip().casefold() for email in values[2].split(",") if email.strip()),
                   values[3].rstrip("/"))


class AccessDenied(Exception):
    """No verified, permitted Cloudflare identity."""


class AccessUnavailable(Exception):
    """Signing keys could not be obtained; access must stay closed."""


class AccessVerifier:
    def __init__(self, config: AccessConfig, *, jwks_client: Any = None) -> None:
        self.config = config
        self.jwks = jwks_client or PyJWKClient(
            config.team_domain + "/cdn-cgi/access/certs", cache_jwk_set=True,
            cache_keys=False, lifespan=300, timeout=4,
        )

    def verify(self, token: str) -> dict[str, str]:
        if not token or len(token) > 16_384:
            raise AccessDenied()
        try:
            header = jwt.get_unverified_header(token)
            # Reject unsupported algorithms before any key lookup. Never trust jku/x5u.
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
                raise AccessDenied()
            key = self.jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=["RS256"],
                                issuer=self.config.team_domain, audience=self.config.audience,
                                options={"require": ["exp", "iat", "iss", "aud", "sub", "email"]})
            email = claims.get("email")
            if not isinstance(email, str) or email.casefold() not in self.config.allowed_emails or not claims["sub"]:
                raise AccessDenied()
            return {"email": email.casefold(), "subject": claims["sub"]}
        except PyJWKClientConnectionError as exc:
            raise AccessUnavailable() from exc
        except (PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise AccessDenied() from exc


class CloudflareAccessMiddleware:
    """Verify the gateway identity, then supply the API token only inside ASGI.

    Existing script API tokens keep working. With Access enabled, unauthenticated
    HTML, exports, documentation and WebSockets are closed, including legacy
    public-route exceptions. Local readiness probes use the API token.
    """
    def __init__(self, app: ASGIApp, *, config: AccessConfig | None = None,
                 api_token: str = "", verifier: AccessVerifier | None = None) -> None:
        self.app = app
        self.config = config
        self.api_token = api_token
        if config and not api_token:
            raise ValueError("EVA_API_TOKEN is required with Cloudflare Access")
        self.verifier = verifier or (AccessVerifier(config) if config else None)
        self.slots = asyncio.Semaphore(4)

    async def _reject(self, scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013 if status == 503 else 4401 if status == 401 else 4403})
        else:
            await JSONResponse({"detail": detail}, status_code=status,
                               headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.config or scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        supplied = headers.get("x-api-token", "")
        api_authenticated = bool(supplied) and secrets.compare_digest(supplied.encode("utf-8"), self.api_token.encode("utf-8"))
        child = dict(scope)
        if not api_authenticated:
            token = headers.get("cf-access-jwt-assertion", "")
            if not token:
                await self._reject(scope, receive, send, 401, "Cloudflare Access login required")
                return
            # Origin guards browser writes/upgrades; scripts with a real API key
            # do not rely on browser cookies and use the separate path above.
            origin = headers.get("origin")
            if (scope["type"] == "websocket" or scope.get("method") not in {"GET", "HEAD", "OPTIONS"}) and origin and origin != self.config.public_origin:
                await self._reject(scope, receive, send, 403, "request origin is not allowed")
                return
            try:
                await asyncio.wait_for(self.slots.acquire(), timeout=0.25)
            except TimeoutError:
                await self._reject(scope, receive, send, 503, "Access verification busy; retry later")
                return
            try:
                identity = await asyncio.to_thread(self.verifier.verify, token)
            except AccessUnavailable:
                await self._reject(scope, receive, send, 503, "Access verification unavailable")
                return
            except AccessDenied:
                await self._reject(scope, receive, send, 403, "Cloudflare Access identity rejected")
                return
            finally:
                self.slots.release()
            child["state"] = {**scope.get("state", {}), "access_identity": identity}
            child["headers"] = [(key, value) for key, value in scope["headers"] if key.lower() != b"x-api-token"]
            child["headers"].append((b"x-api-token", self.api_token.encode("utf-8")))

        async def private_response(message: dict) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                replaced = {b"cache-control", b"cdn-cache-control", b"cloudflare-cdn-cache-control", b"referrer-policy"}
                message["headers"] = [(key, value) for key, value in message.get("headers", []) if key.lower() not in replaced]
                message["headers"] += [(b"cache-control", b"private, no-store"), (b"cdn-cache-control", b"no-store"),
                                       (b"cloudflare-cdn-cache-control", b"no-store"), (b"referrer-policy", b"no-referrer")]
            await send(message)

        await self.app(child, receive, private_response)
