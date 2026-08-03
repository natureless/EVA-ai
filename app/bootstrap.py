"""EVA composition root and runtime lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import time
from typing import Any, Callable

from app.composition import (
    build_agents,
    build_executors,
    build_identity,
    build_memory,
    build_policy,
    build_snapshot_payload,
    build_world,
    create_initial_state,
    ensure_runtime_dirs,
    load_constitution,
    start_github_poller,
)
from app.config import settings
from app.container import (
    AgentSubsystem,
    AppContainer,
    IntegrationSubsystem,
    MemorySubsystem,
    PersonaSubsystem,
    RuntimeSubsystem,
    WorldSubsystem,
)
from app.experimental import start_mvsc, stop_mvsc
from core.cognition_loop import CognitionLoop
from core.planner import Planner
from core.prediction import PredictionTracker
from core.proactive_engine import ProactiveEngine
from core.tool_registry import ToolRegistry, create_builtin_tools
from event.event_bus import EventBus
from persona.repository import PersonaRepository
from persona.service import PersonaService
from runtime.diagnostics import DiagnosticReport, RecoveryActions, SystemDiagnostic
from runtime.agent_worker import ThreadAgentWorkerBackend
from runtime.health import HealthService
from runtime.logging_setup import configure_logging, shutdown_logging
from runtime.result_registry import ResultRegistry
from runtime.scheduler import RuntimeScheduler

logger = logging.getLogger("eva.bootstrap")


def bootstrap_system(ws_manager: Any = None) -> AppContainer:
    """Build and start one EVA runtime.

    Component creation is delegated to ``app.composition``. This function owns
    only dependency ordering and process lifecycle.
    """
    ensure_runtime_dirs(settings)
    configure_logging(
        log_dir=settings.log_dir,
        log_file=settings.log_file,
        level=settings.log_level,
    )
    started_at = time.perf_counter()
    logger.info("bootstrap start env=%s port=%s", settings.env, settings.port)
    _validate_runtime_security()

    if ws_manager:
        ws_manager.capture_loop()

    state = create_initial_state()
    constitution = load_constitution(settings)

    memory = build_memory(settings, state)
    policy = build_policy(settings, constitution)
    state["policy_state"] = policy.get_state()
    executor = build_executors(settings, memory.store, constitution)

    identity = build_identity(settings, state)
    persona_repository = PersonaRepository(memory.store)
    persona_service = PersonaService(persona_repository)
    world = build_world(settings, state, memory.tiered, persona_service)

    event_bus = EventBus(s5_store=memory.tiered.s5)
    state["event_bus_ready"] = True
    planner = Planner(embedding_service=memory.embedding_service)
    state["planner_ready"] = True

    tool_registry = _build_tool_registry(
        memory.tiered,
        executor.executors,
        workspace_root=settings.base_dir,
    )
    _initialize_entity_extractor()
    agents = build_agents(
        state,
        tool_registry=tool_registry,
        base_dir=settings.base_dir,
        llm_max_retries=settings.llm_max_retries,
    )

    result_registry = ResultRegistry()
    state["result_registry_ready"] = True
    proactive_engine = ProactiveEngine(
        stagnation_threshold_sec=settings.stagnation_threshold_sec,
        reminder_cooldown_sec=settings.reminder_cooldown_sec,
    )
    state["proactive_ready"] = True
    _sync_world_state(state, world.model)

    save_snapshot = _snapshot_callback(
        state=state,
        world=world,
        identity=identity,
        persona_service=persona_service,
        tiered_memory=memory.tiered,
    )
    prediction_tracker = PredictionTracker(max_history=50, decay_lambda=0.1)
    worker_backend = ThreadAgentWorkerBackend(
        agents.orchestrator,
        max_workers=settings.agent_worker_count,
        timeout_sec=settings.agent_execution_timeout_sec,
    )

    loop = CognitionLoop(
        event_bus=event_bus,
        memory_api=memory.api,
        memory_governor=memory.governor,
        planner=planner,
        agent_router=agents.router,
        orchestrator=agents.orchestrator,
        result_registry=result_registry,
        proactive_engine=proactive_engine,
        proactive_state=world.proactive_state,
        world_model=world.model,
        system_state=state,
        poll_timeout_sec=settings.queue_poll_timeout_sec,
        result_ttl_sec=settings.result_ttl_sec,
        context_builder=world.context_builder,
        prediction_tracker=prediction_tracker,
        self_model_store=identity.self_model_store,
        self_model=identity.self_model,
        policy_engine=policy,
        tiered_memory=memory.tiered,
        executors=executor.executors,
        ws_manager=ws_manager,
        worker_count=settings.cognition_worker_count,
        agent_worker_backend=worker_backend,
    )
    scheduler = RuntimeScheduler(
        event_bus=event_bus,
        snapshot_save_fn=save_snapshot,
        system_state=state,
        tick_interval_sec=settings.scheduler_tick_interval_sec,
        maintenance_interval_sec=settings.scheduler_maintenance_interval_sec,
        snapshot_interval_sec=settings.scheduler_snapshot_interval_sec,
    )
    health = HealthService(
        state,
        store=memory.store,
        event_bus=event_bus,
        loop=loop,
        scheduler=scheduler,
    )

    container = AppContainer(
        settings=settings,
        system_state=state,
        memory=MemorySubsystem(
            store=memory.store,
            api=memory.api,
            repository=memory.repository,
            governor=memory.governor,
            tiered=memory.tiered,
            embedding_service=memory.embedding_service,
            vector_store=memory.vector_store,
        ),
        persona=PersonaSubsystem(
            profile_store=identity.profile_store,
            self_model_store=identity.self_model_store,
            profile=identity.profile,
            self_model=identity.self_model,
            repository=persona_repository,
            service=persona_service,
        ),
        world=WorldSubsystem(
            context_builder=world.context_builder,
            snapshot_store=world.snapshot_store,
            model=world.model,
            proactive_state=world.proactive_state,
        ),
        agents=AgentSubsystem(
            planner=planner,
            registry=agents.registry,
            router=agents.router,
            orchestrator=agents.orchestrator,
            proactive_engine=proactive_engine,
            prediction_tracker=prediction_tracker,
            tool_registry=tool_registry,
        ),
        runtime=RuntimeSubsystem(
            event_bus=event_bus,
            results=result_registry,
            loop=loop,
            scheduler=scheduler,
            policy=policy,
            worker_backend=worker_backend,
            executors=executor.executors,
            executor_audit_log=executor.audit_log,
            websocket=ws_manager,
            save_snapshot=save_snapshot,
            health=health,
            recovery=RecoveryActions(),
        ),
        integrations=IntegrationSubsystem(),
    )

    try:
        _start_runtime(container)
        diagnostic = _run_boot_diagnostic(container)
        container.diagnostic = diagnostic
        state["diagnostic"] = diagnostic.to_dict()
        _log_diagnostic(diagnostic)
        if diagnostic.overall == "critical":
            failed = diagnostic.failed_checks()
            raise RuntimeError(
                f"Boot diagnostic critical (score={diagnostic.score}): "
                + "; ".join(f"{check.name}: {check.detail}" for check in failed)
            )

        container.mvsc_components = start_mvsc(container, settings)
        bootstrap_sec = round(time.perf_counter() - started_at, 2)
        state.update({
            "ready": True,
            "storage_backend": settings.storage_backend,
            "bootstrap_sec": bootstrap_sec,
        })
        logger.info("bootstrap complete; system ready (%.1fs)", bootstrap_sec)
        return container
    except Exception:
        logger.exception("bootstrap failed; releasing initialized components")
        shutdown_system(container)
        raise


def shutdown_system(container: AppContainer) -> None:
    """Stop runtime services and persist state using best-effort cleanup."""
    logger.info("shutdown start")
    actions: tuple[tuple[str, Callable[[], Any]], ...] = (
        ("github poller", lambda: container.github_poller and container.github_poller.stop()),
        ("scheduler", container.scheduler.shutdown),
        ("cognition loop", container.loop.stop),
        ("agent worker", container.runtime.worker_backend.shutdown),
        ("vector index", lambda: container.vector_store and container.vector_store.save()),
        (
            "session memory",
            lambda: container.tiered_memory.s1.dump_to_s2(container.tiered_memory.s2),
        ),
        ("runtime snapshot", container.save_runtime_snapshot),
        ("MVSC", lambda: stop_mvsc(container.mvsc_components)),
        ("storage", container.store.close),
    )
    for name, action in actions:
        try:
            result = action()
            if name == "session memory":
                logger.info("session memory persisted (%d entries)", result or 0)
            else:
                logger.info("%s stopped", name)
        except Exception:
            logger.exception("%s shutdown failed", name)

    container.system_state["ready"] = False
    container.system_state["scheduler_running"] = False
    logger.info("shutdown complete")
    shutdown_logging()


def _validate_runtime_security() -> None:
    if settings.env.lower() in {"prod", "production"} and not os.environ.get("EVA_API_TOKEN"):
        raise RuntimeError("EVA_API_TOKEN is required in production")


def _build_tool_registry(
    tiered_memory: Any,
    executors: dict[str, Any],
    workspace_root: Path,
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in create_builtin_tools(
        tiered_memory=tiered_memory,
        executors=executors,
        enable_code=settings.enable_code_tool,
        enable_network=settings.enable_network_tools,
        workspace_root=workspace_root,
    ):
        registry.register(tool)
    logger.info("tool registry initialized with %d tools", len(registry.list_all()))
    return registry


def _initialize_entity_extractor() -> None:
    from core.entity_extractor import _lazy_init_llm

    _lazy_init_llm()


def _sync_world_state(state: dict[str, Any], world_model: Any) -> None:
    state.update({
        "focus": world_model.focus,
        "mode": world_model.mode,
        "active_tasks": world_model.active_tasks,
        "last_reply": world_model.last_reply,
        "last_selected_agent": world_model.last_selected_agent,
        "last_loop_id": world_model.last_loop_id,
        "last_loop_at": world_model.last_loop_at,
    })


def _snapshot_callback(
    *,
    state: dict[str, Any],
    world: Any,
    identity: Any,
    persona_service: PersonaService,
    tiered_memory: Any,
) -> Callable[[], None]:
    def save() -> None:
        payload = build_snapshot_payload(
            world_model=world.model,
            profile=identity.profile,
            self_model=identity.self_model,
            proactive_state=world.proactive_state,
            tiered_memory=tiered_memory,
        )
        payload["persona"] = persona_service.get_active_persona().model_dump(mode="json")
        world.snapshot_store.save_latest(payload)
        state["last_snapshot_at"] = datetime.now(timezone.utc).isoformat()
        logger.debug("snapshot saved")

    return save


def _start_runtime(container: AppContainer) -> None:
    container.loop.start()
    container.system_state["loop_ready"] = True
    logger.info("cognition loop started")

    container.github_poller = start_github_poller(
        event_bus=container.event_bus,
        token=settings.github_api_token,
        poll_repos=settings.github_poll_repos,
        interval_sec=settings.github_poll_interval_sec,
    )
    container.scheduler.start()
    container.system_state["scheduler_ready"] = True
    logger.info("scheduler started (tick=%ss)", settings.scheduler_tick_interval_sec)


def _run_boot_diagnostic(container: AppContainer) -> DiagnosticReport:
    return SystemDiagnostic().run_full(
        container.store,
        container.tiered_memory,
        container.system_state,
        snapshot_path=Path(settings.latest_snapshot_path),
    )


def _log_diagnostic(diagnostic: DiagnosticReport) -> None:
    failed_count = len(diagnostic.failed_checks())
    log = logger.info
    if diagnostic.overall == "critical":
        log = logger.critical
    elif diagnostic.overall == "degraded":
        log = logger.warning
    log(
        "boot diagnostic: score=%d overall=%s issues=%d",
        diagnostic.score,
        diagnostic.overall,
        failed_count,
    )
