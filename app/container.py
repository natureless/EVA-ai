"""Typed application container — replaces the god-object dict from bootstrap."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from core.tool_registry import ToolRegistry

    from agent_os.orchestrator import AgentOrchestrator
    from agent_os.registry import AgentRegistry
    from agent_os.router import AgentRouter
    from app.config import Settings
    from core.cognition_loop import CognitionLoop
    from core.context_builder import ContextBuilder
    from core.executor import ExecutorAuditLog
    from core.planner import Planner
    from core.policy_engine import PolicyEngine
    from core.prediction import PredictionTracker
    from core.proactive_engine import ProactiveEngine
    from event.event_bus import EventBus
    from memory.memory_api import MemoryAPI
    from memory.memory_governor import MemoryGovernor, MemoryRepository
    from memory.profile_store import ProfileStore
    from memory.storage_adapter import BaseStorageAdapter
    from memory.tiered_store import TieredMemoryManager
    from persona.repository import PersonaRepository
    from persona.self_model_store import SelfModelStore
    from persona.service import PersonaService
    from runtime.diagnostics import DiagnosticReport, RecoveryActions
    from runtime.health import HealthService
    from runtime.result_registry import ResultRegistry
    from runtime.scheduler import RuntimeScheduler
    from runtime.websocket import WebSocketManager
    from world.snapshot_store import SnapshotStore
    from world.world_model import WorldModelGraph


@dataclass
class AppContainer:
    """Holds all initialized EVA system components with proper types.

    Replaces the untyped dict returned by bootstrap_system().
    Access components via attribute syntax (container.loop) instead of
    string keys (container["loop"]).
    """

    # ── config ──
    settings: Settings
    system_state: dict[str, Any]

    # ── storage ──
    store: BaseStorageAdapter
    memory_api: MemoryAPI
    memory_repository: MemoryRepository
    memory_governor: MemoryGovernor
    tiered_memory: TieredMemoryManager

    # ── persona ──
    profile_store: ProfileStore
    self_model_store: SelfModelStore
    profile: dict[str, Any]
    self_model: dict[str, Any]
    persona_repo: PersonaRepository
    persona_service: PersonaService

    # ── world ──
    context_builder: ContextBuilder
    snapshot_store: SnapshotStore
    world_model: WorldModelGraph
    proactive_state: dict[str, Any]

    # ── messaging ──
    event_bus: EventBus

    # ── agent runtime ──
    planner: Planner
    registry: AgentRegistry
    result_registry: ResultRegistry
    proactive_engine: ProactiveEngine
    agent_router: AgentRouter
    orchestrator: AgentOrchestrator

    # ── execution ──
    loop: CognitionLoop
    scheduler: RuntimeScheduler
    prediction_tracker: PredictionTracker
    policy_engine: PolicyEngine

    # ── optional / late-bound ──
    ws_manager: WebSocketManager | None = None
    executors: dict[str, Any] = field(default_factory=dict)
    executor_audit_log: ExecutorAuditLog | None = None
    tool_registry: ToolRegistry | None = None
    save_runtime_snapshot: Callable[[], None] = field(default=lambda: None)
    health: HealthService | None = None
    diagnostic: DiagnosticReport | None = None
    recovery_actions: RecoveryActions | None = None
    github_poller: Any = None
    embedding_service: Any = None
    vector_store: Any = None
    mvsc_components: dict[str, Any] | None = None
