from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/agents")
def list_agents(request: Request) -> dict:
    return {"agents": request.app.state.container.registry.list_agents()}
