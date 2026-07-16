"""EVA system bootstrap — initialize all components and wire them together."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

import yaml

from agent_os.orchestrator import AgentOrchestrator
from agent_os.registry import AgentRegistry
from agent_os.router import AgentRouter
from agents.chat_agent import ChatAgent
from agents.coding_agent import CodingAgent
from agents.docs_agent import DocsAgent
from agents.search_agent import SearchAgent
from app.config import settings
from app.container import AppContainer
from core.cognition_loop import CognitionLoop
from core.context_builder import ContextBuilder
from core.executor import (
    APIExecutor,
    BrowserExecutor,
    CodeExecutor,
    CommsExecutor,
    ExecutorAuditLog,
    FileExecutor,
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
from persona.repository import PersonaRepository
from persona.self_model_store import SelfModelStore
from persona.service import PersonaService
from runtime.diagnostics import RecoveryActions, SystemDiagnostic
from runtime.health import HealthService
from runtime.logging_setup import configure_logging
from runtime.result_registry import ResultRegistry
from runtime.scheduler import RuntimeScheduler
from world.snapshot_store import SnapshotStore
from world.world_model import WorldModelGraph

logger = logging.getLogger("eva.bootstrap")


# ── helpers ────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    settings.template_dir.mkdir(parents=True, exist_ok=True)
    settings.static_dir.mkdir(parents=True, exist_ok=True)
    settings.snapshot_dir.mkdir(parents=True, exist_ok=True)


def _load_constitution() -> dict[str, Any]:
    """Load constitution.yaml as the canonical rule source.

    Returns an empty dict if the file is missing or unparseable.
    This is the base layer — config/*.yaml files can override.
    """
    path = Path("constitution.yaml")
    if not path.exists():
        logger.warning("constitution.yaml not found — using defaults")
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        logger.info("constitution.yaml loaded (version=%s)", data.get("version", "?"))
        return data
    except Exception:
        logger.exception("failed to load constitution.yaml")
        return {}


def _create_initial_state() -> dict[str, Any]:
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


def _deep_merge(base: dict, override: dict) -> None:
    """Merge override into base in-place (nested dicts merged, not replaced)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ── subsystem initializers ─────────────────────────────────────────

def _init_storage(state: dict[str, Any]) -> dict[str, Any]:
    """Initialize SQLite store, memory API, governor, and tiered memory."""
    store = SQLiteStore(Path(settings.db_path))
    store.init_db()
    state["db_ready"] = True
    logger.info("database initialized at %s", settings.db_path)

    tiered_memory = TieredMemoryManager(store, config={
        "S1_session": {"max_entries": 200, "ttl_minutes": 30},
        "S2_working": {"max_entries": 500, "ttl_hours": 72},
        "S3_long_term": {"max_entries": 10000},
    })

    return {
        "store": store,
        "memory_api": MemoryAPI(store),
        "memory_repository": MemoryRepository(store),
        "memory_governor": MemoryGovernor(MemoryRepository(store), tiered_memory=tiered_memory),
        "tiered_memory": tiered_memory,
    }


def _init_persona(state: dict[str, Any]) -> dict[str, Any]:
    """Load profile and self-model from disk."""
    profile_store = ProfileStore(Path(settings.profile_path))
    self_model_store = SelfModelStore(Path(settings.self_model_path))

    profile = profile_store.load_or_init()
    self_model = self_model_store.load_or_init()
    state["profile_ready"] = True
    state["persona_ready"] = True
    state["self_model_ready"] = True
    logger.info("profile and self-model loaded")

    return {
        "profile_store": profile_store,
        "self_model_store": self_model_store,
        "profile": profile,
        "self_model": self_model,
    }


def _init_world(
    state: dict[str, Any],
    tiered_memory: TieredMemoryManager,
    persona_service: PersonaService,
) -> dict[str, Any]:
    """Load or create world model from snapshot/S4 store."""
    snapshot_store = SnapshotStore(
        snapshot_dir=Path(settings.snapshot_dir),
        latest_snapshot_path=Path(settings.latest_snapshot_path),
    )
    snapshot = snapshot_store.load_latest()

    if snapshot and isinstance(snapshot, dict) and "world_model" in snapshot:
        world_model = WorldModelGraph.from_dict(snapshot["world_model"])
        world_model.load_from_store(tiered_memory.s4)
        logger.info("snapshot loaded (entities=%d, edges=%d)",
                    world_model.entity_count, world_model.edge_count)
    else:
        world_model = WorldModelGraph()
        world_model.load_from_store(tiered_memory.s4)
        logger.info("no snapshot; loaded %d entities, %d edges from S4",
                    world_model.entity_count, world_model.edge_count)
    state["snapshot_ready"] = True

    context_builder = ContextBuilder(
        persona_service=persona_service,
        tiered_memory=tiered_memory,
        world_model=world_model,
    )

    proactive_state: dict[str, Any] = (
        snapshot.get("proactive_state", {}) if snapshot else {}
    )
    proactive_state.setdefault("last_user_message_ts", None)
    proactive_state.setdefault("last_reminder_ts", None)

    return {
        "snapshot_store": snapshot_store,
        "world_model": world_model,
        "context_builder": context_builder,
        "proactive_state": proactive_state,
    }


def _init_agents(state: dict[str, Any]) -> dict[str, Any]:
    """Register built-in agents and create router + orchestrator."""
    registry = AgentRegistry()
    registry.register(ChatAgent())
    registry.register(DocsAgent())
    registry.register(SearchAgent())
    registry.register(CodingAgent())
    state["registry_ready"] = True
    state["agents"] = registry.list_agents()
    logger.info("agent registry initialized with %d agents", len(registry.list_agents()))

    return {
        "registry": registry,
        "agent_router": AgentRouter(registry),
        "orchestrator": AgentOrchestrator(registry),
    }


def _init_policy(constitution: dict[str, Any] | None = None) -> PolicyEngine:
    """Load policy configuration and create PolicyEngine.

    Merges constitution.yaml (base layer) with config/policy.yaml (override layer).
    """
    const = constitution or {}
    policy_config: dict[str, Any] = {}

    # base layer: constitution state_machine
    const_sm = const.get("state_machine", {})
    if const_sm:
        policy_config["state_machine"] = const_sm

    # base layer: constitution priority_system
    const_ps = const.get("priority_system", {})
    if const_ps:
        policy_config["priority_system"] = const_ps

    # override layer: config/policy.yaml
    policy_config_path = Path("config/policy.yaml")
    if policy_config_path.exists():
        with policy_config_path.open("r", encoding="utf-8") as fh:
            override = yaml.safe_load(fh) or {}
        _deep_merge(policy_config, override)

    engine = PolicyEngine(policy_config)
    engine.transition("user_command")
    engine.transition("command_completed")
    logger.info("policy engine initialized (state=%s)", engine.get_state())
    return engine


def _init_executors(store: SQLiteStore, constitution: dict[str, Any] | None = None) -> dict[str, Any]:
    """Initialize the executor framework (file, code, browser, api, comms).

    Merges constitution.yaml boundaries (base layer) with config/executors.yaml (override).
    """
    const = constitution or {}
    audit_log = ExecutorAuditLog(store)
    executors_config: dict[str, Any] = {}

    # base layer: constitution boundaries → executor limits
    boundaries = const.get("boundaries", {})
    fs_boundary = boundaries.get("filesystem", {})
    compute_boundary = boundaries.get("compute", {})

    if fs_boundary:
        executors_config.setdefault("executors", {})
        executors_config["executors"].setdefault("file", {})
        file_cfg = executors_config["executors"]["file"]
        file_cfg.setdefault("limits", {})
        if fs_boundary.get("max_file_size_mb"):
            file_cfg["limits"]["max_file_size_mb"] = fs_boundary["max_file_size_mb"]
        if fs_boundary.get("forbidden_paths"):
            file_cfg["forbidden_paths"] = fs_boundary["forbidden_paths"]

    if compute_boundary:
        executors_config.setdefault("executors", {})
        executors_config["executors"].setdefault("code", {})
        code_cfg = executors_config["executors"]["code"]
        code_cfg.setdefault("limits", {})
        if compute_boundary.get("max_process_duration_minutes"):
            code_cfg["limits"]["timeout"] = compute_boundary["max_process_duration_minutes"] * 60

    # override layer: config/executors.yaml
    exec_cfg_path = Path("config/executors.yaml")
    if exec_cfg_path.exists():
        with exec_cfg_path.open("r", encoding="utf-8") as fh:
            override = yaml.safe_load(fh) or {}
        _deep_merge(executors_config, override)

    executors = {
        "file": FileExecutor(audit_log, executors_config),
        "code": CodeExecutor(audit_log, executors_config),
        "browser": BrowserExecutor(audit_log, executors_config),
        "api": APIExecutor(audit_log, executors_config),
        "comms": CommsExecutor(audit_log, executors_config),
    }
    logger.info("executor framework initialized (%d executors)", len(executors))
    return {"executor_audit_log": audit_log, "executors": executors}


def _build_snapshot_payload(
    world_model: WorldModelGraph,
    profile: dict[str, Any],
    self_model: dict[str, Any],
    proactive_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": "0.1",
        "world_model": world_model.to_dict(),
        "profile": profile,
        "self_model": self_model,
        "proactive_state": proactive_state,
    }


# ── main bootstrap ─────────────────────────────────────────────────

def bootstrap_system(ws_manager: Any = None) -> AppContainer:
    """Initialize the EVA system and return a typed AppContainer.

    Sets up directories, logging, database, event bus, agents, executors,
    cognition loop, and scheduler. All components are accessible via
    attribute access on the returned container.
    """
    _ensure_dirs()
    configure_logging(
        log_dir=settings.log_dir,
        log_file=settings.log_file,
        level=settings.log_level,
    )
    logger.info("bootstrap start env=%s port=%s", settings.env, settings.port)

    system_state = _create_initial_state()
    constitution = _load_constitution()

    # ── storage layer ──
    storage = _init_storage(system_state)
    store = storage["store"]

    # ── persona layer ──
    persona = _init_persona(system_state)
    persona_repo = PersonaRepository(store)
    persona_service = PersonaService(persona_repo)

    # ── world layer ──
    world = _init_world(system_state, storage["tiered_memory"], persona_service)

    # ── restore S1 session memory from S2 ──
    restored = storage["tiered_memory"].s1.restore_from_s2(storage["tiered_memory"].s2)
    if restored:
        logger.info("restored %d session entries from S2", restored)

    # ── messaging ──
    event_bus = EventBus(s5_store=storage["tiered_memory"].s5)
    system_state["event_bus_ready"] = True
    logger.info("event bus initialized")

    # ── planner ──
    planner = Planner()
    system_state["planner_ready"] = True

    # ── agents ──
    agents = _init_agents(system_state)

    # ── results ──
    result_registry = ResultRegistry()
    system_state["result_registry_ready"] = True

    # ── proactive engine ──
    proactive_engine = ProactiveEngine(
        stagnation_threshold_sec=settings.stagnation_threshold_sec,
        reminder_cooldown_sec=settings.reminder_cooldown_sec,
    )
    system_state["proactive_ready"] = True

    # ── sync world state → system_state ──
    wm = world["world_model"]
    system_state["focus"] = wm.focus
    system_state["mode"] = wm.mode
    system_state["active_tasks"] = wm.active_tasks
    system_state["last_reply"] = wm.last_reply
    system_state["last_selected_agent"] = wm.last_selected_agent
    system_state["last_loop_id"] = wm.last_loop_id
    system_state["last_loop_at"] = wm.last_loop_at

    # ── snapshot callback ──
    def save_runtime_snapshot() -> None:
        persona_profile = persona_service.get_active_persona()
        payload = _build_snapshot_payload(
            world_model=wm,
            profile=persona["profile"],
            self_model=persona["self_model"],
            proactive_state=world["proactive_state"],
        )
        payload["persona"] = persona_profile.model_dump(mode="json")
        world["snapshot_store"].save_latest(payload)
        system_state["last_snapshot_at"] = datetime.now(timezone.utc).isoformat()
        logger.debug("snapshot saved")

    # ── prediction tracker ──
    prediction_tracker = PredictionTracker(max_history=50, decay_lambda=0.1)

    # ── policy engine ──
    policy_engine = _init_policy(constitution)
    system_state["policy_state"] = policy_engine.get_state()

    # ── executors ──
    exec_data = _init_executors(store, constitution)

    # ── cognition loop ──
    loop = CognitionLoop(
        event_bus=event_bus,
        memory_api=storage["memory_api"],
        memory_governor=storage["memory_governor"],
        planner=planner,
        agent_router=agents["agent_router"],
        orchestrator=agents["orchestrator"],
        result_registry=result_registry,
        proactive_engine=proactive_engine,
        proactive_state=world["proactive_state"],
        world_model=wm,
        system_state=system_state,
        poll_timeout_sec=settings.queue_poll_timeout_sec,
        result_ttl_sec=settings.result_ttl_sec,
        context_builder=world["context_builder"],
        prediction_tracker=prediction_tracker,
        self_model_store=persona["self_model_store"],
        self_model=persona["self_model"],
        policy_engine=policy_engine,
        tiered_memory=storage["tiered_memory"],
        executors=exec_data["executors"],
        ws_manager=ws_manager,
    )
    loop.start()
    system_state["loop_ready"] = True
    logger.info("cognition loop started")

    # ── scheduler ──
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
    logger.info("scheduler started (tick=%ss)", settings.scheduler_tick_interval_sec)

    # ── health ──
    health = HealthService(
        system_state,
        store=store,
        event_bus=event_bus,
        loop=loop,
        scheduler=scheduler,
    )
    system_state["ready"] = True

    # ── boot diagnostic ──
    diagnostic = SystemDiagnostic().run_full(
        store, storage["tiered_memory"], system_state,
        snapshot_path=Path(settings.latest_snapshot_path),
    )
    system_state["diagnostic"] = diagnostic.to_dict()
    _log_diagnostic(diagnostic)

    logger.info("bootstrap complete — system ready")

    return AppContainer(
        settings=settings,
        system_state=system_state,
        store=store,
        memory_api=storage["memory_api"],
        memory_repository=storage["memory_repository"],
        memory_governor=storage["memory_governor"],
        tiered_memory=storage["tiered_memory"],
        profile_store=persona["profile_store"],
        self_model_store=persona["self_model_store"],
        profile=persona["profile"],
        self_model=persona["self_model"],
        persona_repo=persona_repo,
        persona_service=persona_service,
        context_builder=world["context_builder"],
        snapshot_store=world["snapshot_store"],
        world_model=wm,
        proactive_state=world["proactive_state"],
        event_bus=event_bus,
        ws_manager=ws_manager,
        planner=planner,
        registry=agents["registry"],
        result_registry=result_registry,
        proactive_engine=proactive_engine,
        agent_router=agents["agent_router"],
        orchestrator=agents["orchestrator"],
        loop=loop,
        scheduler=scheduler,
        prediction_tracker=prediction_tracker,
        policy_engine=policy_engine,
        executors=exec_data["executors"],
        executor_audit_log=exec_data["executor_audit_log"],
        save_runtime_snapshot=save_runtime_snapshot,
        health=health,
        diagnostic=diagnostic,
        recovery_actions=RecoveryActions(),
    )


def shutdown_system(container: AppContainer) -> None:
    """Gracefully shutdown all system components."""
    logger.info("shutdown start")
    try:
        if container.scheduler:
            container.scheduler.shutdown()
            logger.info("scheduler shutdown")

        if container.loop:
            container.loop.stop()
            logger.info("cognition loop stopped")

        # dump S1 session memory to S2 for persistence across restarts
        if container.tiered_memory:
            count = container.tiered_memory.s1.dump_to_s2(container.tiered_memory.s2)
            logger.info("S1 session memory dumped to S2 (%d entries)", count)

        container.save_runtime_snapshot()
        logger.info("snapshot saved on shutdown")

        container.system_state["ready"] = False
        logger.info("shutdown complete")
    except Exception:
        logger.exception("error during shutdown")
        raise


# ── internal helpers ───────────────────────────────────────────────

def _log_diagnostic(diagnostic: SystemDiagnostic) -> None:
    if diagnostic.overall == "critical":
        logger.critical("boot diagnostic: score=%d overall=%s issues=%d",
                        diagnostic.score, diagnostic.overall,
                        len(diagnostic.failed_checks()))
    elif diagnostic.overall == "degraded":
        logger.warning("boot diagnostic: score=%d overall=%s issues=%d",
                       diagnostic.score, diagnostic.overall,
                       len(diagnostic.failed_checks()))
    else:
        logger.info("boot diagnostic: score=%d overall=%s",
                    diagnostic.score, diagnostic.overall)
