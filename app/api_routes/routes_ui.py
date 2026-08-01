from typing import Any

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.config import settings


router = APIRouter()
templates = Jinja2Templates(directory=str(settings.template_dir))


@router.get("/")
def index(request: Request) -> Any:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": settings.app_name, "page": "chat"},
    )


@router.get("/settings")
def settings_page(request: Request) -> Any:
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"app_name": settings.app_name, "page": "settings"},
    )


@router.get("/memory")
def memory_explorer_page(request: Request) -> Any:
    return templates.TemplateResponse(
        request=request,
        name="memory.html",
        context={"app_name": settings.app_name, "page": "memory"},
    )
