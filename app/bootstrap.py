from datetime import datetime, timezone
import logging
from pathlib import Path
import yaml

from agent_os.orchestrator import AgentOrchestrator
from agent_os.registry import AgentRegistry
from agent_os.router import AgentRouter
from agents.chat_agent import ChatAgent
from agents.coding_agent import CodingAgent
from agents.docs_agent import DocsAgent
from agents.search_agent import SearchAgent
from app.config import settings
from core.cognition_loop import CognitionLoop
from core.context_builder import ContextBuilder
from core.executor import (
    ExecutorAuditLog, FileExecutor, CodeExecutor,
    BrowserExecutor, APIExecutor, CommsExecutor,
)
from core.planner import Planner
from core.policy_engine import PolicyEngine
from core.prediction import PredictionTracker
from core.proactive_engine import ProactiveEngine
from event.event_bus import EventBus
from memory.memory_api import MemoryAPI
from memory.memory_governor import MemoryGovernor, MemoryRepository
from memory.profile_store import ProfileStore
from memory.sqlite_store import SQLiteStore
from memory.tiered_store import TieredMemoryManager
from persona.persona_store import PersonaStore
from persona.repository import PersonaRepository
from persona.service import PersonaService
from persona.self_model_store import SelfModelStore
from runtime.health import HealthService
from runtime.diagnostics import SystemDiagnostic, RecoveryActions
from runtime.logging_setup import configure_logging
from runtime.result_registry import ResultRegistry
from runtime.scheduler import RuntimeScheduler
from world.snapshot_store import SnapshotStore
from world.world_model import WorldModelGraph


def ensure_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    settings.template_dir.mkdir(parents=True, exist_ok=True)
    settings.static_dir.mkdir(parents=True, exist_ok=True)
    settings.snapshot_dir.mkdir(parents=True, exist_ok=True)


def create_initial_state() -> dict:
    return {
        "ready": False,
        "db_ready": False,
        "event_bus_ready": False,
        "planner_ready": False,
        "loop_ready": False,
        "registry_ready": False,
        "result_registry_ready": False,
        "snapshot_ready": False,
        "profile_ready": False,
        "persona_ready": False,
        "self_model_ready": False,
        "scheduler_ready": False,
        "proactive_ready": False,
        "scheduler_running": False,
        "focus": "idle",
        "mode": "active",
        "active_tasks": [],
        "pending_events": 0,
        "pending_results": 0,
        "last_reply": "",
        "last_selected_agent": "",
        "last_loop_id": "",
        "last_loop_at": None,
        "last_snapshot_at": None,
        "last_proactive_reason": None,
        "last_context_summary": None,
        "agents": [],
    }


def build_snapshot_payload(
    *,
    world_model: WorldModelGraph,
    profile: dict,
    persona: dict,
    self_model: dict,
    proactive_state: dict,
) -> dict:
    return {
        "version": "0.1",
        "world_model": world_model.to_dict(),
        "profile": profile,
        "persona": persona,
        "self_model": self_model,
        "proactive_state": proactive_state,
    }


def bootstrap_system() -> dict:
    """Initialize the EVA system with all components.
    
    Sets up directories, logging, database, event bus, agents, and schedulers.
    Returns a container dict with all initialized components.
    """
    ensure_dirs()
    configure_logging(
        log_dir=settings.log_dir,
        log_file=settings.log_file,
        level=settings.log_level,
    )
    logger = logging.getLogger("eva.bootstrap")
    logger.info("bootstrap start with env=%s port=%s", settings.env, settings.port)

    system_state = create_initial_state()

    try:
        store = SQLiteStore(Path(settings.db_path))
        store.init_db()
        system_state["db_ready"] = True
        logger.info("database initialized at %s", settings.db_path)
    except Exception as e:
        logger.error("failed to initialize database: %s", e, exc_info=True)
        raise

    memory_api = MemoryAPI(store)
    memory_repository = MemoryRepository(store)
    memory_governor = MemoryGovernor(memory_repository)

    # init tiered memory manager (S1-S5)
    tiered_memory = TieredMemoryManager(store, config={
        "S1_session": {"max_entries": 200, "ttl_minutes": 30},
        "S2_working": {"max_entries": 500, "ttl_hours": 72},
        "S3_long_term": {"max_entries": 10000},
    })
    persona_repo = PersonaRepository(store)
    persona_service = PersonaService(persona_repo)

    profile_store = ProfileStore(Path(settings.profile_path))
    persona_store = PersonaStore(Path(settings.persona_path))
    self_model_store = SelfModelStore(Path(settings.self_model_path))

    try:
        profile = profile_store.load_or_init()
        persona = persona_store.load_or_init()
        self_model = self_model_store.load_or_init()
        system_state["profile_ready"] = True
        system_state["persona_ready"] = True
        system_state["self_model_ready"] = True
        logger.info("profile, persona, and self-model loaded")
    except Exception as e:
        logger.error("failed to load profile/persona/self_model: %s", e, exc_info=True)
        raise

    snapshot_store = SnapshotStore(
        snapshot_dir=Path(settings.snapshot_dir),
        latest_snapshot_path=Path(settings.latest_snapshot_path),
    )
    snapshot = snapshot_store.load_latest()
    if snapshot and isinstance(snapshot, dict) and "world_model" in snapshot:
        world_model = WorldModelGraph.from_dict(snapshot["world_model"])
        world_model.load_from_store(tiered_memory.s4)
        logger.info("snapshot loaded from %s (entities=%d, edges=%d)",
                    settings.latest_snapshot_path,
                    world_model.entity_count, world_model.edge_count)
    else:
        world_model = WorldModelGraph()
        world_model.load_from_store(tiered_memory.s4)
        logger.info("no snapshot found; loaded %d entities, %d edges from S4",
                    world_model.entity_count, world_model.edge_count)
    system_state["snapshot_ready"] = True

    proactive_state = snapshot.get("proactive_state", {}) if snapshot else {}
    proactive_state.setdefault("last_user_message_ts", None)
    proactive_state.setdefault("last_reminder_ts", None)

    # context builder — after world_model and tiered_memory are ready
    context_builder = ContextBuilder(
        persona_service=persona_service,
        tiered_memory=tiered_memory,
        world_model=world_model,
    )

    event_bus = EventBus()
    system_state["event_bus_ready"] = True
    logger.info("event bus initialized")

    planner = Planner()
    system_state["planner_ready"] = True
    logger.info("planner initialized")

    registry = AgentRegistry()
    registry.register(ChatAgent())
    registry.register(DocsAgent())
    registry.register(SearchAgent())
    registry.register(CodingAgent())
    system_state["registry_ready"] = True
    system_state["agents"] = registry.list_agents()
    logger.info("agent registry initialized with %d agents", len(registry.list_agents()))

    result_registry = ResultRegistry()
    system_state["result_registry_ready"] = True
    logger.info("result registry initialized")

    proactive_engine = ProactiveEngine(
        stagnation_threshold_sec=settings.stagnation_threshold_sec,
        reminder_cooldown_sec=settings.reminder_cooldown_sec,
    )
    system_state["proactive_ready"] = True
    logger.info("proactive engine initialized")

    agent_router = AgentRouter(registry)
    orchestrator = AgentOrchestrator(registry)

    system_state["focus"] = world_model.focus
    system_state["mode"] = world_model.mode
    system_state["active_tasks"] = world_model.active_tasks
    system_state["last_reply"] = world_model.last_reply
    system_state["last_selected_agent"] = world_model.last_selected_agent
    system_state["last_loop_id"] = world_model.last_loop_id
    system_state["last_loop_at"] = world_model.last_loop_at

    def save_runtime_snapshot() -> None:
        payload = build_snapshot_payload(
            world_model=world_model,
            profile=profile,
            persona=persona,
            self_model=self_model,
            proactive_state=proactive_state,
        )
        snapshot_store.save_latest(payload)
        system_state["last_snapshot_at"] = datetime.now(timezone.utc).isoformat()
        logger.debug("snapshot saved")

    prediction_tracker = PredictionTracker(max_history=50, decay_lambda=0.1)

    # Load policy configuration
    policy_config: dict = {}
    policy_config_path = Path("config/policy.yaml")
    if policy_config_path.exists():
        with policy_config_path.open("r", encoding="utf-8") as fh:
            policy_config = yaml.safe_load(fh) or {}
    policy_engine = PolicyEngine(policy_config)
    policy_engine.transition("user_command")   # enter Commanded on boot
    policy_engine.transition("command_completed")  # settle to Dormant
    system_state["policy_state"] = policy_engine.get_state()
    logger.info("policy engine initialized")

    # init executor framework
    executor_audit_log = ExecutorAuditLog(store)
    executors_config: dict = {}
    exec_cfg_path = Path("config/executors.yaml")
    if exec_cfg_path.exists():
        with exec_cfg_path.open("r", encoding="utf-8") as fh:
            executors_config = yaml.safe_load(fh) or {}
    executors = {
        "file": FileExecutor(executor_audit_log, executors_config),
        "code": CodeExecutor(executor_audit_log, executors_config),
        "browser": BrowserExecutor(executor_audit_log, executors_config),
        "api": APIExecutor(executor_audit_log, executors_config),
        "comms": CommsExecutor(executor_audit_log, executors_config),
    }
    logger.info("executor framework initialized (%d executors)", len(executors))

    try:
        loop = CognitionLoop(
            event_bus=event_bus,
            memory_api=memory_api,
            memory_governor=memory_governor,
            planner=planner,
            agent_router=agent_router,
            orchestrator=orchestrator,
            result_registry=result_registry,
            proactive_engine=proactive_engine,
            proactive_state=proactive_state,
            world_model=world_model,
            system_state=system_state,
            poll_timeout_sec=settings.queue_poll_timeout_sec,
            result_ttl_sec=settings.result_ttl_sec,
            context_builder=context_builder,
            enable_v02_pipeline=settings.enable_v02_pipeline,
            prediction_tracker=prediction_tracker,
            self_model_store=self_model_store,
            self_model=self_model,
            policy_engine=policy_engine,
            tiered_memory=tiered_memory,
            executors=executors,
        )
        loop.start()
        system_state["loop_ready"] = True
        logger.info("cognition loop started")
    except Exception as e:
        logger.error("failed to start cognition loop: %s", e, exc_info=True)
        raise

    try:
        scheduler = RuntimeScheduler(
            event_bus=event_bus,
            snapshot_save_fn=save_runtime_snapshot,
            system_state=system_state,
            tick_interval_sec=settings.scheduler_tick_interval_sec,
            maintenance_interval_sec=settings.scheduler_maintenance_interval_sec,
            snapshot_interval_sec=settings.scheduler_snapshot_interval_sec,
        )
        scheduler.start()
        system_state["scheduler_ready"] = True
        logger.info("scheduler started with tick_interval=%s sec", settings.scheduler_tick_interval_sec)
    except Exception as e:
        logger.error("failed to start scheduler: %s", e, exc_info=True)
        raise

    health = HealthService(system_state)
    system_state["ready"] = True
    logger.info("bootstrap complete - system ready")

    # ── run startup diagnostic ──────────────────────────────
    diagnostic = SystemDiagnostic().run_full(
        store, tiered_memory, system_state,
        snapshot_path=Path(settings.latest_snapshot_path),
    )
    system_state["diagnostic"] = diagnostic.to_dict()
    if diagnostic.overall == "critical":
        logger.critical("boot diagnostic: score=%d overall=%s issues=%d",
                        diagnostic.score, diagnostic.overall, len(diagnostic.failed_checks()))
    elif diagnostic.overall == "degraded":
        logger.warning("boot diagnostic: score=%d overall=%s issues=%d",
                       diagnostic.score, diagnostic.overall, len(diagnostic.failed_checks()))
    else:
        logger.info("boot diagnostic: score=%d overall=%s", diagnostic.score, diagnostic.overall)

    return {
        "settings": settings,
        "system_state": system_state,
        "store": store,
        "memory_api": memory_api,
        "memory_repository": memory_repository,
        "memory_governor": memory_governor,
        "profile_store": profile_store,
        "persona_store": persona_store,
        "self_model_store": self_model_store,
        "profile": profile,
        "persona": persona,
        "self_model": self_model,
        "persona_repo": persona_repo,
        "persona_service": persona_service,
        "context_builder": context_builder,
        "snapshot_store": snapshot_store,
        "world_model": world_model,
        "proactive_state": proactive_state,
        "event_bus": event_bus,
        "planner": planner,
        "registry": registry,
        "result_registry": result_registry,
        "proactive_engine": proactive_engine,
        "agent_router": agent_router,
        "orchestrator": orchestrator,
        "loop": loop,
        "scheduler": scheduler,
        "prediction_tracker": prediction_tracker,
        "policy_engine": policy_engine,
        "tiered_memory": tiered_memory,
        "executors": executors,
        "executor_audit_log": executor_audit_log,
        "save_runtime_snapshot": save_runtime_snapshot,
        "health": health,
        "diagnostic": diagnostic,
        "recovery_actions": RecoveryActions(),
    }


def shutdown_system(container: dict) -> None:
    """Gracefully shutdown all system components.
    
    Stops the scheduler and cognition loop, saves final snapshot.
    """
    logger = logging.getLogger("eva.bootstrap")
    logger.info("shutdown start")
    try:
        scheduler = container.get("scheduler")
        if scheduler:
            scheduler.shutdown()
            logger.info("scheduler shutdown")

        loop = container.get("loop")
        if loop:
            loop.stop()
            logger.info("cognition loop stopped")

        save_runtime_snapshot = container.get("save_runtime_snapshot")
        if save_runtime_snapshot:
            save_runtime_snapshot()
            logger.info("snapshot saved on shutdown")

        container["system_state"]["ready"] = False
        logger.info("shutdown complete")
    except Exception as e:
        logger.error("error during shutdown: %s", e, exc_info=True)
        raise
