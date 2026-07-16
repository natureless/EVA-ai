import json
import logging
import time as _time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.bootstrap import bootstrap_system, shutdown_system
from app.config import settings
from runtime.websocket import ws_manager


logger = logging.getLogger("eva.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("application startup")
    container = bootstrap_system()
    container["ws_manager"] = ws_manager
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


@app.websocket("/ws")
async def websocket_endpoint(ws):
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
