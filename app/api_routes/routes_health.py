from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/health/live")
def health_live(request: Request) -> dict:
    container = request.app.state.container
    return container["health"].live()


@router.get("/health/ready")
def health_ready(request: Request) -> dict:
    container = request.app.state.container
    return container["health"].ready()
