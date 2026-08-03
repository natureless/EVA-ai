import json
import logging
import os
import secrets
import time as _time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

# Load .env before any other module reads os.environ
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

from app.api import router
from app.bootstrap import bootstrap_system, shutdown_system
from app.config import settings
from runtime.rate_limiter import RateLimitMiddleware, SlidingWindowLimiter
from runtime.auth import AuthMiddleware
from runtime.websocket import WebSocketManager


logger = logging.getLogger("eva.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    logger.info("application startup")
    ws_manager = WebSocketManager()
    container = bootstrap_system(ws_manager=ws_manager)
    app.state.container = container
    try:
        yield
    finally:
        logger.info("application shutdown")
        shutdown_system(container)


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
app.include_router(router)

_rate_limiter = SlidingWindowLimiter()
app.add_middleware(AuthMiddleware)
app.add_middleware(
    RateLimitMiddleware,
    limiter=_rate_limiter,
    trust_proxy_headers=settings.trust_proxy_headers,
)
app.state.rate_limiter = _rate_limiter


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    expected_token = os.environ.get("EVA_API_TOKEN", "")
    if expected_token:
        provided_token = (
            ws.headers.get("X-API-Token", "")
            or ws.query_params.get("token", "")
        )
        if not secrets.compare_digest(provided_token, expected_token):
            await ws.close(code=4401, reason="authentication required")
            return

    ws_manager = app.state.container.ws_manager
    channel = ws.query_params.get("channel", "")
    await ws_manager.connect(ws, channel=channel)
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"pong": True, "ts": _time.time()}))
    except Exception:
        pass
    finally:
        ws_manager.disconnect(ws)


logger.info("FastAPI application initialized with environment=%s", settings.env)
