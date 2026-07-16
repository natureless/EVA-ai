from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/scheduler/jobs")
def scheduler_jobs(request: Request) -> dict:
    container = request.app.state.container
    scheduler = container["scheduler"]
    return {"jobs": scheduler.list_jobs()}
