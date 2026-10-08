"""EVA composition root and runtime lifecycle."""

from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
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
    schedule_memory_reindex_if_stale,
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
from app.experimental import (
    build_business_goals,
    build_durable_requests,
    build_minimal_brain,
    build_processing_episodes,
    start_mvsc,
    stop_mvsc,
)
from core.cognition_loop import CognitionLoop
from core.event_processor import EventProcessor
from core.planner import Planner
from core.prediction import PredictionTracker
from core.proactive_engine import ProactiveEngine
from core.tool_registry import ToolRegistry, create_builtin_tools
from event.event_bus import EventBus
from persona.repository import PersonaRepository
from persona.service import PersonaService
from runtime.diagnostics import DiagnosticReport, RecoveryActions, SystemDiagnostic
from runtime.agent_worker import create_agent_worker_backend, worker_is_idle
from runtime.controller import RuntimeController, ShutdownStep
from runtime.health import HealthService
from runtime.logging_setup import configure_logging, shutdown_logging
from runtime.result_registry import ResultRegistry
from runtime.request_recovery import RequestRecoveryPublisher
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
    with ExitStack() as rollback:
        resources = _ConstructionResources(rollback)
        resources.add("logging", shutdown_logging)
        try:
            _validate_runtime_security()
            container = _compose_container(ws_manager, resources)
            container.runtime.controller = _build_runtime_controller(container)
        except BaseException:
            logger.exception(
                "bootstrap construction failed; releasing prepared resources"
            )
            raise
        # The controller now owns every live dependency. The construction stack
        # must never close them again on normal startup or runtime failure.
        rollback.pop_all()

    state = container.system_state
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
        schedule_memory_reindex_if_stale(
            container.store,
            container.tiered_memory,
            container.runtime.worker_backend.submit,
            container.memory.embedding_service,
            container.memory.vector_store,
        )
        if container.runtime.request_recovery is not None:
            container.runtime.request_recovery.start()
        bootstrap_sec = round(time.perf_counter() - started_at, 2)
        state.update(
            {
                "ready": True,
                "storage_backend": settings.storage_backend,
                "bootstrap_sec": bootstrap_sec,
            }
        )
        logger.info("bootstrap complete; system ready (%.1fs)", bootstrap_sec)
        return container
    except BaseException:
        logger.exception("bootstrap failed; releasing initialized components")
        try:
            shutdown_system(container)
        except Exception:
            logger.exception(
                "startup cleanup failed; preserving the original startup error"
            )
        raise


class _ConstructionResources:
    """Rollback only untransferred resources without hiding the startup error."""

    def __init__(self, stack: ExitStack) -> None:
        self._stack = stack
        self._blocked = False

    def add(self, name: str, action: Callable[[], Any]) -> None:
        self._stack.callback(self._release, name, action)

    def _release(self, name: str, action: Callable[[], Any]) -> None:
        if self._blocked:
            logger.error("construction cleanup preserved dependent resource: %s", name)
            return
        try:
            if action() is False:
                raise RuntimeError("resource still has active work")
        except Exception:
            self._blocked = True
            logger.exception(
                "construction cleanup failed for %s; dependencies preserved", name
            )


def _close_constructed_worker(worker_backend: Any) -> bool:
    if not worker_is_idle(worker_backend):
        return False
    worker_backend.shutdown()
    return True


def _compose_container(
    ws_manager: Any, resources: _ConstructionResources
) -> AppContainer:
    """Construct without starting consumers; register resources as they return."""
    if ws_manager:
        ws_manager.capture_loop()

    state = create_initial_state()
    constitution = load_constitution(settings)

    memory = build_memory(settings, state)
    resources.add("storage", memory.store.close)
    policy = build_policy(settings, constitution)
    state["policy_state"] = policy.get_state()
    executor = build_executors(settings, memory.store, constitution)

    identity = build_identity(settings, state)
    persona_repository = PersonaRepository(memory.store)
    persona_service = PersonaService(persona_repository)
    world = build_world(settings, state, memory.tiered, persona_service)

    event_bus = EventBus(
        s5_store=memory.tiered.s5,
        overflow_policy="drop_newest"
        if settings.enable_minimal_brain
        else "drop_oldest",
    )
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

    durable_requests = build_durable_requests(settings)
    if durable_requests is not None:
        resources.add("durable_requests", durable_requests.close)
    business_goals = build_business_goals(
        settings,
        recover=not (
            settings.enable_processing_episodes or settings.enable_durable_requests
        ),
    )
    if business_goals is not None:
        resources.add("business_goals", business_goals.close)

    def existing_receipt(task_id, event_id):
        known = (
            durable_requests.summary_for_task(task_id, event_id)
            if durable_requests is not None
            else None
        )
        if known is None and business_goals is not None:
            known = business_goals.processing_receipt_for_task(task_id, event_id)
        return known

    processing_episodes = build_processing_episodes(
        settings, receipt_lookup=existing_receipt
    )
    if processing_episodes is not None:
        resources.add("processing_episodes", processing_episodes.close)
        agents.orchestrator.tool_recorder = processing_episodes
        if business_goals is not None:
            processing_episodes.attach_receipt_sink(business_goals.record_receipt)

    if durable_requests is not None:

        def audit_receipt(task_id):
            known = (
                processing_episodes.receipt_for_task(task_id)
                if processing_episodes is not None
                else None
            )
            if known is None and business_goals is not None:
                # Read the registered event binding from the request ledger.
                record = durable_requests.pending_identity(task_id)
                known = business_goals.processing_receipt_for_task(
                    task_id, record["event_id"]
                )
            return known

        if settings.resume_durable_requests:
            durable_requests.prepare_resume(receipt_lookup=audit_receipt)
        else:
            while durable_requests.recover(receipt_lookup=audit_receipt):
                pass
        if business_goals is not None:
            durable_requests.attach_receipt_sink(business_goals.record_receipt)
            business_goals.recover_receipts(durable_requests.summary_for_task)
    if business_goals is not None and (
        processing_episodes is not None or durable_requests is not None
    ):
        business_goals.recover(
            unclaimed_lookup=durable_requests.unclaimed_event
            if settings.resume_durable_requests and durable_requests is not None
            else None
        )

    def observe_receipt(receipt):
        errors = []
        for observer in (processing_episodes, business_goals):
            if observer is not None:
                try:
                    observer.record_receipt(receipt)
                except Exception as error:
                    errors.append(error)
        if errors:
            raise RuntimeError("receipt persistence unavailable") from None

    result_registry = ResultRegistry(
        retention_sec=settings.result_ttl_sec,
        pending_timeout_sec=settings.result_pending_timeout_sec,
        max_entries=settings.result_registry_capacity,
        persistence=durable_requests,
        receipt_observer=observe_receipt
        if business_goals is not None or processing_episodes is not None
        else None,
    )
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
    worker_backend = create_agent_worker_backend(agents.orchestrator, settings)
    resources.add("agent_worker", lambda: _close_constructed_worker(worker_backend))

    processor = EventProcessor(
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
        result_ttl_sec=settings.result_ttl_sec,
        context_builder=world.context_builder,
        prediction_tracker=prediction_tracker,
        self_model_store=identity.self_model_store,
        self_model=identity.self_model,
        policy_engine=policy,
        tiered_memory=memory.tiered,
        executors=executor.executors,
        ws_manager=ws_manager,
        agent_worker_backend=worker_backend,
        episode_recorder=processing_episodes,
    )
    resources.add("event_processor", processor.request_stop)
    loop = CognitionLoop(
        event_bus=event_bus,
        processor=processor,
        poll_timeout_sec=settings.queue_poll_timeout_sec,
        worker_count=settings.cognition_worker_count,
    )
    resources.add("cognition_loop", loop.stop)
    scheduler = RuntimeScheduler(
        event_bus=event_bus,
        snapshot_save_fn=save_snapshot,
        system_state=state,
        tick_interval_sec=settings.scheduler_tick_interval_sec,
        maintenance_interval_sec=settings.scheduler_maintenance_interval_sec,
        snapshot_interval_sec=settings.scheduler_snapshot_interval_sec,
    )
    resources.add("scheduler", scheduler.shutdown)
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
            processor=processor,
            executors=executor.executors,
            executor_audit_log=executor.audit_log,
            websocket=ws_manager,
            save_snapshot=save_snapshot,
            health=health,
            recovery=RecoveryActions(),
        ),
        integrations=IntegrationSubsystem(
            business_goals=business_goals,
            processing_episodes=processing_episodes,
            durable_requests=durable_requests,
        ),
    )

    resources.add(
        "minimal_consumer",
        lambda: container.integrations.minimal_brain is None
        or container.integrations.minimal_brain.stop(),
    )
    if settings.resume_durable_requests and durable_requests is not None:
        container.runtime.request_recovery = RequestRecoveryPublisher(
            durable_requests,
            result_registry,
            event_bus,
            accepting=lambda: container.runtime.controller is not None
            and container.runtime.controller.accepting,
        )
        resources.add("request_recovery", container.runtime.request_recovery.stop)
    resources.add("event_bus", event_bus.close)
    return container


def shutdown_system(container: AppContainer) -> bool:
    """Use the selected runtime's ordered, retryable shutdown contract."""
    controller = container.runtime.controller
    if controller is None:
        # Startup may fail before selecting a consumer. This owner only cleans
        # initialized dependencies; it never starts a fallback execution path.
        controller = _build_runtime_controller(
            container, consumer=container.runtime.loop
        )
        container.runtime.controller = controller
    stopped = controller.stop()
    if stopped:
        logger.info("shutdown complete")
        shutdown_logging()
    else:
        logger.error(
            "shutdown incomplete; remaining dependencies preserved: %s",
            controller.snapshot()["shutdown"],
        )
    return stopped


def _build_runtime_controller(
    container: AppContainer, *, consumer: Any = None
) -> RuntimeController:
    config = container.settings
    if consumer is None:
        consumer = (
            build_minimal_brain(container, config)
            if config.enable_minimal_brain
            else container.runtime.loop
        )
    processor = container.runtime.processor
    if processor is None:
        raise RuntimeError("runtime processor unavailable")
    return RuntimeController(
        mode="minimal" if config.enable_minimal_brain else "legacy",
        consumer=consumer,
        processor=processor,
        event_bus=container.runtime.event_bus,
        worker_backend=container.runtime.worker_backend,
        system_state=container.system_state,
        producers=(
            ShutdownStep(
                "request_recovery",
                lambda: container.runtime.request_recovery
                and container.runtime.request_recovery.stop(),
            ),
            ShutdownStep(
                "github_poller",
                lambda: container.github_poller and container.github_poller.stop(),
            ),
            ShutdownStep("mvsc", lambda: stop_mvsc(container.mvsc_components)),
            ShutdownStep("scheduler", container.scheduler.shutdown),
        ),
        finalizers=(
            ShutdownStep("agent_worker", container.runtime.worker_backend.shutdown),
            ShutdownStep(
                "vector_index",
                lambda: container.vector_store and container.vector_store.save(),
            ),
            ShutdownStep(
                "session_memory",
                lambda: container.tiered_memory.s1.dump_to_s2(
                    container.tiered_memory.s2
                ),
            ),
            ShutdownStep("runtime_snapshot", container.save_runtime_snapshot),
            ShutdownStep(
                "durable_requests",
                lambda: container.integrations.durable_requests
                and container.integrations.durable_requests.close(),
            ),
            ShutdownStep(
                "business_goals",
                lambda: container.integrations.business_goals
                and container.integrations.business_goals.close(),
            ),
            ShutdownStep(
                "processing_episodes",
                lambda: container.integrations.processing_episodes
                and container.integrations.processing_episodes.close(),
            ),
            ShutdownStep("storage", container.store.close),
        ),
    )


def _validate_runtime_security() -> None:
    if settings.env.lower() in {"prod", "production"} and not os.environ.get(
        "EVA_API_TOKEN"
    ):
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
        enable_us_market_snapshot=settings.enable_us_market_snapshot,
        workspace_root=workspace_root,
    ):
        registry.register(tool)
    logger.info("tool registry initialized with %d tools", len(registry.list_all()))
    return registry


def _initialize_entity_extractor() -> None:
    from core.entity_extractor import _lazy_init_llm

    _lazy_init_llm()


def _sync_world_state(state: dict[str, Any], world_model: Any) -> None:
    state.update(
        {
            "focus": world_model.focus,
            "mode": world_model.mode,
            "active_tasks": world_model.active_tasks,
            "last_reply": world_model.last_reply,
            "last_selected_agent": world_model.last_selected_agent,
            "last_loop_id": world_model.last_loop_id,
            "last_loop_at": world_model.last_loop_at,
        }
    )


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
        payload["persona"] = persona_service.get_active_persona().model_dump(
            mode="json"
        )
        if isinstance(state.get("minimal_brain"), dict):
            payload["minimal_brain"] = deepcopy(state["minimal_brain"])
        world.snapshot_store.save_latest(payload)
        state["last_snapshot_at"] = datetime.now(timezone.utc).isoformat()
        logger.debug("snapshot saved")

    return save


def _start_runtime(container: AppContainer) -> None:
    controller = container.runtime.controller
    if controller is None:
        controller = _build_runtime_controller(container)
        container.runtime.controller = controller
    if container.health is not None:
        container.health.set_cognition_loop(controller)
    controller.start()
    logger.info("cognitive runtime started")

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
