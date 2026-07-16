from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.config import settings


router = APIRouter()
templates = Jinja2Templates(directory=str(settings.template_dir))


@router.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"app_name": settings.app_name},
    )
