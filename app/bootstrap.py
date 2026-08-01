"""EVA system bootstrap — initialize all components and wire them together."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import time
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
from core.playwright_executor import PlaywrightBrowserExecutor, is_playwright_available
from core.planner import Planner
from core.policy_engine import PolicyEngine
from core.prediction import PredictionTracker
from core.proactive_engine import ProactiveEngine
from core.tool_registry import ToolRegistry, create_builtin_tools
from event.event_bus import EventBus
from memory.memory_api import MemoryAPI
from memory.memory_governor import MemoryGovernor, MemoryRepository
from memory.profile_store import ProfileStore
from memory.sqlite_store import SQLiteStore
from memory.tiered_store import TieredMemoryManager
from persona.repository import PersonaRepository
from persona.self_model_store import SelfModelStore
from persona.service import PersonaService
from runtime.diagnostics import DiagnosticReport, RecoveryActions, SystemDiagnostic
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

    Returns an empty dict if the file is missing (development without a
    constitution is permitted).  Raises RuntimeError when the file exists
    but is unparseable — a broken constitution is a safety risk and must
    be treated as a startup failure.
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
    except yaml.YAMLError as e:
        logger.critical("constitution.yaml is malformed — refusing to start without valid safety boundaries")
        raise RuntimeError(f"Failed to parse constitution.yaml: {e}") from e
    except Exception as e:
        logger.critical("constitution.yaml could not be read: %s", e)
        raise RuntimeError(f"Failed to load constitution.yaml: {e}") from e


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


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Merge override into base in-place (nested dicts merged, not replaced)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ── subsystem initializers ─────────────────────────────────────────

def _wire_memory(state: dict[str, Any]) -> dict[str, Any]:
    """Initialize storage, memory API, governor, tiered memory, vector search, and restore S1 session."""
    # ── storage backend selection ──
    if settings.storage_backend == "postgresql" and settings.database_url:
        from memory.postgres_store import PostgresStore
        store = PostgresStore(database_url=settings.database_url)
        store.init_db()
        state["db_ready"] = True
        logger.info("database initialized (PostgreSQL: %s)", settings.database_url.split("@")[-1] if "@" in settings.database_url else "connected")
    elif settings.storage_backend == "postgresql":
        from memory.postgres_store import PostgresStore
        store = PostgresStore()
        store.init_db()
        state["db_ready"] = True
        logger.info("database initialized (PostgreSQL via env vars)")
    else:
        store = SQLiteStore(Path(settings.db_path))
        store.init_db()
        state["db_ready"] = True
        logger.info("database initialized at %s", settings.db_path)

    # ── vector search (lazy, only when embedding_provider=local) ──
    embedding_service = None
    vector_store = None
    if settings.embedding_provider == "local":
        try:
            from memory.embedding_service import EmbeddingService
            from memory.vector_store import VectorStore
            embedding_service = EmbeddingService(model_name=settings.embedding_model_name)
            vector_store = VectorStore(
                dim=embedding_service.dim,
                index_path=Path(settings.vector_index_path),
            )
            vector_store.load()
            logger.info("vector store loaded (%d vectors)", vector_store.size())
        except ImportError:
            logger.warning(
                "sentence-transformers or faiss not installed — "
                "vector search disabled"
            )
        except Exception:
            logger.exception("failed to initialize vector search — disabling")
            embedding_service = None
            vector_store = None

    tiered_memory = TieredMemoryManager(store, config={
        "S1_session": {"max_entries": 200, "ttl_minutes": 30},
        "S2_working": {"max_entries": 500, "ttl_hours": 72},
        "S3_long_term": {"max_entries": 10000},
    }, embedding_service=embedding_service, vector_store=vector_store)

    governor = MemoryGovernor(MemoryRepository(store), tiered_memory=tiered_memory)
    # wire reverse bridge so tiered ingest feeds back into governor
    tiered_memory._governor = governor

    # staleness check: if DB count diverges from index by >10%, reindex in background
    if vector_store is not None and embedding_service is not None:
        try:
            active_rows = store.fetchall(
                "SELECT COUNT(*) as cnt FROM long_term_memory WHERE status='active'",
                (),
            )
            active_count = active_rows[0]["cnt"] if active_rows else 0
            if active_count > 0 and abs(active_count - vector_store.size()) > active_count * 0.1:
                logger.info(
                    "vector index stale (%d DB vs %d indexed) — scheduling reindex",
                    active_count, vector_store.size(),
                )
                import threading
                from memory.reindex_job import reindex_all
                t = threading.Thread(
                    target=reindex_all,
                    args=(embedding_service, vector_store, tiered_memory.s3),
                    daemon=True,
                    name="eva-reindex",
                )
                t.start()
        except Exception:
            logger.exception("staleness check failed")

    # restore S1 session memory from S2
    restored = tiered_memory.s1.restore_from_s2(tiered_memory.s2)
    if restored:
        logger.info("restored %d session entries from S2", restored)

    return {
        "store": store,
        "memory_api": MemoryAPI(store),
        "memory_repository": MemoryRepository(store),
        "memory_governor": governor,
        "tiered_memory": tiered_memory,
        "embedding_service": embedding_service,
        "vector_store": vector_store,
    }


def _wire_persona(state: dict[str, Any]) -> dict[str, Any]:
    """Load profile, self-model, and create persona service."""
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


def _wire_world(
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


def _wire_agents(state: dict[str, Any], tool_registry: Any = None) -> dict[str, Any]:
    """Register built-in agents and create router + orchestrator."""
    registry = AgentRegistry()
    registry.register(ChatAgent(tool_registry=tool_registry))
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


def _wire_policy(constitution: dict[str, Any] | None = None) -> PolicyEngine:
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


def _wire_executors(store: SQLiteStore, constitution: dict[str, Any] | None = None) -> dict[str, Any]:
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
    network_boundary = boundaries.get("network", {})

    if fs_boundary:
        executors_config.setdefault("executors", {})
        executors_config["executors"].setdefault("file", {})
        file_cfg = executors_config["executors"]["file"]
        file_cfg.setdefault("limits", {})
        if fs_boundary.get("max_file_size_mb"):
            file_cfg["limits"]["max_file_size_mb"] = fs_boundary["max_file_size_mb"]
        if fs_boundary.get("forbidden_paths"):
            file_cfg["forbidden_paths"] = fs_boundary["forbidden_paths"]
        # Append platform-specific forbidden paths so Linux-centric
        # constitution defaults also work on Windows and macOS.
        import platform as _platform
        _plat = _platform.system()
        _existing = file_cfg.setdefault("forbidden_paths", [])
        if _plat == "Windows":
            for _p in ("C:\\Windows", "C:\\Windows\\System32", "C:\\Program Files", "C:\\Program Files (x86)"):
                if _p not in _existing:
                    _existing.append(_p)
        elif _plat == "Darwin":
            for _p in ("/System", "/Library/System", "/private/etc", "/private/var"):
                if _p not in _existing:
                    _existing.append(_p)

    if compute_boundary:
        executors_config.setdefault("executors", {})
        executors_config["executors"].setdefault("code", {})
        code_cfg = executors_config["executors"]["code"]
        code_cfg.setdefault("limits", {})
        if compute_boundary.get("max_process_duration_minutes"):
            code_cfg["limits"]["timeout"] = compute_boundary["max_process_duration_minutes"] * 60

    if network_boundary:
        executors_config.setdefault("executors", {})
        allowed_domains = network_boundary.get("allowed_domains", [])
        allowed_ports = network_boundary.get("allowed_ports", [80, 443])
        if allowed_domains:
            executors_config["executors"].setdefault("browser", {})
            executors_config["executors"]["browser"]["allowed_domains"] = allowed_domains
            executors_config["executors"].setdefault("api", {})
            executors_config["executors"]["api"]["allowed_domains"] = allowed_domains
            executors_config["executors"]["api"]["allowed_ports"] = allowed_ports

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

    # Upgrade to Playwright-based browser if available
    if is_playwright_available():
        try:
            executors["playwright_browser"] = PlaywrightBrowserExecutor(audit_log, executors_config)
            logger.info("playwright browser executor enabled")
        except Exception as e:
            logger.warning("playwright browser executor init failed: %s", e)

    logger.info("executor framework initialized (%d executors)", len(executors))
    return {"executor_audit_log": audit_log, "executors": executors}


def _build_snapshot_payload(
    world_model: WorldModelGraph,
    profile: dict[str, Any],
    self_model: dict[str, Any],
    proactive_state: dict[str, Any],
    tiered_memory: Any = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": "0.1",
        "world_model": world_model.to_dict(),
        "profile": profile,
        "self_model": self_model,
        "proactive_state": proactive_state,
    }

    # ── memory snapshot (S1+S2+S3 summaries, not full DB) ──
    if tiered_memory:
        try:
            s1_entries = tiered_memory.s1.list_all()[:20]
            s2_entries = tiered_memory.s2.list_recent(limit=20)
            s3_entries = tiered_memory.s3.search("", limit=20)  # most recent

            payload["memory"] = {
                "s1_session": [{"key": e[0], "value": e[1]} for e in s1_entries],
                "s2_working": [
                    {"id": r.get("id"), "content": r.get("content", "")[:200],
                     "source": r.get("source", ""), "created_at": r.get("created_at", "")}
                    for r in s2_entries
                ],
                "s3_long_term": [
                    {"id": r.get("id"), "content": r.get("content", "")[:200],
                     "category": r.get("category", ""), "importance": r.get("importance", 0)}
                    for r in s3_entries
                ],
            }
        except Exception:
            pass  # memory snapshot is best-effort; world model is the critical path

    return payload


def _wire_github_poller(
    event_bus: EventBus,
    token: str,
    poll_repos: str,
    interval_sec: int,
) -> Any | None:
    """Create and start a GitHubPoller if credentials are configured."""
    if not token or not poll_repos:
        return None
    try:
        from connectors.github.poller import GitHubPoller
        repos = [r.strip() for r in poll_repos.split(",") if r.strip()]
        if not repos:
            return None
        poller = GitHubPoller(
            event_bus=event_bus,
            token=token,
            repos=repos,
            interval_sec=interval_sec,
        )
        poller.start()
        logger.info("github poller started (%d repos, interval=%ss)", len(repos), interval_sec)
        return poller
    except Exception:
        logger.exception("failed to start github poller")
        return None


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
    _bootstrap_start = time.perf_counter()

    if ws_manager:
        ws_manager.capture_loop()

    system_state = _create_initial_state()
    constitution = _load_constitution()

    # ── storage layer ──
    storage = _wire_memory(system_state)
    store = storage["store"]

    # ── persona layer ──
    persona = _wire_persona(system_state)
    persona_repo = PersonaRepository(store)
    persona_service = PersonaService(persona_repo)

    # ── world layer ──
    world = _wire_world(system_state, storage["tiered_memory"], persona_service)

    # ── messaging ──
    event_bus = EventBus(s5_store=storage["tiered_memory"].s5)
    system_state["event_bus_ready"] = True
    logger.info("event bus initialized")

    # ── planner ──
    planner = Planner(embedding_service=storage.get("embedding_service"))
    system_state["planner_ready"] = True

    # ── tool registry ──
    tool_registry = ToolRegistry()
    builtin_tools = create_builtin_tools(tiered_memory=storage["tiered_memory"])
    for tool in builtin_tools:
        tool_registry.register(tool)
    logger.info("tool registry initialized with %d tools", len(tool_registry.list_all()))

    # ── entity extractor upgrade ──
    # Upgrade the global entity extractor singleton to use LLM when available.
    from core.entity_extractor import _lazy_init_llm
    _lazy_init_llm()

    # ── agents ──
    agents = _wire_agents(system_state, tool_registry=tool_registry)

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
            tiered_memory=storage["tiered_memory"],
        )
        payload["persona"] = persona_profile.model_dump(mode="json")
        world["snapshot_store"].save_latest(payload)
        system_state["last_snapshot_at"] = datetime.now(timezone.utc).isoformat()
        logger.debug("snapshot saved")

    # ── prediction tracker ──
    prediction_tracker = PredictionTracker(max_history=50, decay_lambda=0.1)

    # ── policy engine ──
    policy_engine = _wire_policy(constitution)
    system_state["policy_state"] = policy_engine.get_state()

    # ── executors ──
    exec_data = _wire_executors(store, constitution)

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
        worker_count=settings.cognition_worker_count,
    )
    loop.start()
    system_state["loop_ready"] = True
    logger.info("cognition loop started")

    # ── github poller ──
    github_poller = _wire_github_poller(
        event_bus=event_bus,
        token=settings.github_api_token,
        poll_repos=settings.github_poll_repos,
        interval_sec=settings.github_poll_interval_sec,
    )

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
    system_state["storage_backend"] = settings.storage_backend

    # ── boot diagnostic ──
    diagnostic = SystemDiagnostic().run_full(
        store, storage["tiered_memory"], system_state,
        snapshot_path=Path(settings.latest_snapshot_path),
    )
    system_state["diagnostic"] = diagnostic.to_dict()
    _log_diagnostic(diagnostic)

    if diagnostic.overall == "critical":
        failed = diagnostic.failed_checks()
        raise RuntimeError(
            f"Boot diagnostic critical (score={diagnostic.score}): "
            + "; ".join(f"{c.name}: {c.detail}" for c in failed)
        )

    bootstrap_sec = round(time.perf_counter() - _bootstrap_start, 2)
    logger.info("bootstrap complete — system ready (%.1fs)", bootstrap_sec)
    system_state["bootstrap_sec"] = bootstrap_sec

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
        tool_registry=tool_registry,
        save_runtime_snapshot=save_runtime_snapshot,
        health=health,
        diagnostic=diagnostic,
        recovery_actions=RecoveryActions(),
        github_poller=github_poller,
        embedding_service=storage.get("embedding_service"),
        vector_store=storage.get("vector_store"),
    )


def shutdown_system(container: AppContainer) -> None:
    """Gracefully shutdown all system components."""
    logger.info("shutdown start")
    try:
        if container.github_poller:
            container.github_poller.stop()
            logger.info("github poller stopped")

        if container.scheduler:
            container.scheduler.shutdown()
            logger.info("scheduler shutdown")

        if container.loop:
            container.loop.stop()
            logger.info("cognition loop stopped")

        # persist vector index
        if container.vector_store:
            container.vector_store.save()
            logger.info("vector index saved")

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

def _log_diagnostic(diagnostic: DiagnosticReport) -> None:
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
