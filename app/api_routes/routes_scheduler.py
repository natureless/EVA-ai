from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/api/scheduler/jobs")
def scheduler_jobs(request: Request) -> dict:
    return {"jobs": request.app.state.container.scheduler.list_jobs()}
