from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/agents")
def list_agents(request: Request) -> dict:
    container = request.app.state.container
    registry = container["registry"]
    return {"agents": registry.list_agents()}
