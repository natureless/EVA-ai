"""Typed subsystem containers for one running EVA instance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
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
    from core.tool_registry import ToolRegistry
    from event.event_bus import EventBus
    from memory.memory_api import MemoryAPI
    from memory.memory_governor import MemoryGovernor, MemoryRepository
    from memory.profile_store import ProfileStore
    from memory.storage_adapter import BaseStorageAdapter
    from memory.tiered_store import TieredMemoryManager
    from persona.repository import PersonaRepository
    from persona.self_model_store import SelfModelStore
    from persona.service import PersonaService
    from runtime.agent_worker import AgentWorkerBackend
    from runtime.diagnostics import DiagnosticReport, RecoveryActions
    from runtime.health import HealthService
    from runtime.result_registry import ResultRegistry
    from runtime.scheduler import RuntimeScheduler
    from runtime.websocket import WebSocketManager
    from world.snapshot_store import SnapshotStore
    from world.world_model import WorldModelGraph


@dataclass(slots=True)
class MemorySubsystem:
    store: BaseStorageAdapter
    api: MemoryAPI
    repository: MemoryRepository
    governor: MemoryGovernor
    tiered: TieredMemoryManager
    embedding_service: Any = None
    vector_store: Any = None


@dataclass(slots=True)
class PersonaSubsystem:
    profile_store: ProfileStore
    self_model_store: SelfModelStore
    profile: dict[str, Any]
    self_model: dict[str, Any]
    repository: PersonaRepository
    service: PersonaService


@dataclass(slots=True)
class WorldSubsystem:
    context_builder: ContextBuilder
    snapshot_store: SnapshotStore
    model: WorldModelGraph
    proactive_state: dict[str, Any]


@dataclass(slots=True)
class AgentSubsystem:
    planner: Planner
    registry: AgentRegistry
    router: AgentRouter
    orchestrator: AgentOrchestrator
    proactive_engine: ProactiveEngine
    prediction_tracker: PredictionTracker
    tool_registry: ToolRegistry


@dataclass(slots=True)
class RuntimeSubsystem:
    event_bus: EventBus
    results: ResultRegistry
    loop: CognitionLoop
    scheduler: RuntimeScheduler
    policy: PolicyEngine
    worker_backend: AgentWorkerBackend
    executors: dict[str, Any] = field(default_factory=dict)
    executor_audit_log: ExecutorAuditLog | None = None
    websocket: WebSocketManager | None = None
    save_snapshot: Callable[[], None] = field(default=lambda: None)
    health: HealthService | None = None
    diagnostic: DiagnosticReport | None = None
    recovery: RecoveryActions | None = None


@dataclass(slots=True)
class IntegrationSubsystem:
    github_poller: Any = None
    mvsc: dict[str, Any] | None = None


@dataclass
class AppContainer:
    """Runtime service graph with temporary flat compatibility properties."""

    settings: Settings
    system_state: dict[str, Any]
    memory: MemorySubsystem
    persona: PersonaSubsystem
    world: WorldSubsystem
    agents: AgentSubsystem
    runtime: RuntimeSubsystem
    integrations: IntegrationSubsystem = field(default_factory=IntegrationSubsystem)

    # Storage compatibility
    @property
    def store(self) -> BaseStorageAdapter:
        return self.memory.store

    @property
    def memory_api(self) -> MemoryAPI:
        return self.memory.api

    @property
    def memory_repository(self) -> MemoryRepository:
        return self.memory.repository

    @property
    def memory_governor(self) -> MemoryGovernor:
        return self.memory.governor

    @property
    def tiered_memory(self) -> TieredMemoryManager:
        return self.memory.tiered

    @property
    def embedding_service(self) -> Any:
        return self.memory.embedding_service

    @property
    def vector_store(self) -> Any:
        return self.memory.vector_store

    # Persona compatibility
    @property
    def profile_store(self) -> ProfileStore:
        return self.persona.profile_store

    @property
    def self_model_store(self) -> SelfModelStore:
        return self.persona.self_model_store

    @property
    def profile(self) -> dict[str, Any]:
        return self.persona.profile

    @property
    def self_model(self) -> dict[str, Any]:
        return self.persona.self_model

    @property
    def persona_repo(self) -> PersonaRepository:
        return self.persona.repository

    @property
    def persona_service(self) -> PersonaService:
        return self.persona.service

    # World compatibility
    @property
    def context_builder(self) -> ContextBuilder:
        return self.world.context_builder

    @property
    def snapshot_store(self) -> SnapshotStore:
        return self.world.snapshot_store

    @property
    def world_model(self) -> WorldModelGraph:
        return self.world.model

    @property
    def proactive_state(self) -> dict[str, Any]:
        return self.world.proactive_state

    # Agent compatibility
    @property
    def planner(self) -> Planner:
        return self.agents.planner

    @property
    def registry(self) -> AgentRegistry:
        return self.agents.registry

    @property
    def agent_router(self) -> AgentRouter:
        return self.agents.router

    @property
    def orchestrator(self) -> AgentOrchestrator:
        return self.agents.orchestrator

    @property
    def proactive_engine(self) -> ProactiveEngine:
        return self.agents.proactive_engine

    @property
    def prediction_tracker(self) -> PredictionTracker:
        return self.agents.prediction_tracker

    @property
    def tool_registry(self) -> ToolRegistry:
        return self.agents.tool_registry

    # Runtime compatibility
    @property
    def event_bus(self) -> EventBus:
        return self.runtime.event_bus

    @property
    def result_registry(self) -> ResultRegistry:
        return self.runtime.results

    @property
    def loop(self) -> CognitionLoop:
        return self.runtime.loop

    @property
    def scheduler(self) -> RuntimeScheduler:
        return self.runtime.scheduler

    @property
    def policy_engine(self) -> PolicyEngine:
        return self.runtime.policy

    @property
    def executors(self) -> dict[str, Any]:
        return self.runtime.executors

    @property
    def executor_audit_log(self) -> ExecutorAuditLog | None:
        return self.runtime.executor_audit_log

    @property
    def ws_manager(self) -> WebSocketManager | None:
        return self.runtime.websocket

    @property
    def save_runtime_snapshot(self) -> Callable[[], None]:
        return self.runtime.save_snapshot

    @property
    def health(self) -> HealthService | None:
        return self.runtime.health

    @property
    def diagnostic(self) -> DiagnosticReport | None:
        return self.runtime.diagnostic

    @diagnostic.setter
    def diagnostic(self, value: DiagnosticReport | None) -> None:
        self.runtime.diagnostic = value

    @property
    def recovery_actions(self) -> RecoveryActions | None:
        return self.runtime.recovery

    # Integration compatibility
    @property
    def github_poller(self) -> Any:
        return self.integrations.github_poller

    @github_poller.setter
    def github_poller(self, value: Any) -> None:
        self.integrations.github_poller = value

    @property
    def mvsc_components(self) -> dict[str, Any] | None:
        return self.integrations.mvsc

    @mvsc_components.setter
    def mvsc_components(self, value: dict[str, Any] | None) -> None:
        self.integrations.mvsc = value
