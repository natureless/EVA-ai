from fastapi import APIRouter

from app.api_routes.routes_agents import router as agents_router
from app.api_routes.routes_chat import router as chat_router
from app.api_routes.routes_executors import router as executors_router
from app.api_routes.routes_github import router as github_router
from app.api_routes.routes_health import router as health_router
from app.api_routes.routes_memory import router as memory_router
from app.api_routes.routes_metrics import router as metrics_router
from app.api_routes.routes_persona import router as persona_router
from app.api_routes.routes_policy import router as policy_router
from app.api_routes.routes_proactive import router as proactive_router
from app.api_routes.routes_scheduler import router as scheduler_router
from app.api_routes.routes_snapshot import router as snapshot_router
from app.api_routes.routes_ui import router as ui_router


router = APIRouter()

router.include_router(ui_router)
router.include_router(chat_router)
router.include_router(executors_router)
router.include_router(memory_router)
router.include_router(agents_router)
router.include_router(persona_router)
router.include_router(policy_router)
router.include_router(scheduler_router)
router.include_router(snapshot_router)
router.include_router(proactive_router)
router.include_router(health_router)
router.include_router(metrics_router)
router.include_router(github_router)
