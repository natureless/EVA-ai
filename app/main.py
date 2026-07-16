import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.bootstrap import bootstrap_system, shutdown_system
from app.config import settings


logger = logging.getLogger("eva.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup and shutdown.
    
    Bootstraps the entire system on startup and performs graceful shutdown
    on application termination.
    """
    logger.info("application startup")
    container = bootstrap_system()
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

logger.info("FastAPI application initialized with environment=%s", settings.env)

