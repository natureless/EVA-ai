"""Typed component factories for the EVA composition root.

This module creates subsystems but never starts the cognition loop or scheduler.
Startup and shutdown ordering remain the responsibility of ``app.bootstrap``.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
import logging
from pathlib import Path
import platform
import threading
from typing import Any

import yaml

from agent_os.orchestrator import AgentOrchestrator
from agent_os.registry import AgentRegistry
from agent_os.router import AgentRouter
from agents.chat_agent import ChatAgent
from agents.coding_agent import CodingAgent
from agents.docs_agent import DocsAgent
from agents.search_agent import SearchAgent
from app.config import Settings
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
from core.policy_engine import PolicyEngine
from event.event_bus import EventBus
from memory.memory_api import MemoryAPI
from memory.memory_governor import MemoryGovernor, MemoryRepository
from memory.profile_store import ProfileStore
from memory.sqlite_store import SQLiteStore
from memory.storage_adapter import BaseStorageAdapter
from memory.tiered_store import TieredMemoryManager
from persona.self_model_store import SelfModelStore
from persona.service import PersonaService
from world.snapshot_store import SnapshotStore
from world.world_model import WorldModelGraph

logger = logging.getLogger("eva.composition")


@dataclass(slots=True)
class MemoryComponents:
    store: BaseStorageAdapter
    api: MemoryAPI
    repository: MemoryRepository
    governor: MemoryGovernor
    tiered: TieredMemoryManager
    embedding_service: Any = None
    vector_store: Any = None


@dataclass(slots=True)
class IdentityComponents:
    profile_store: ProfileStore
    self_model_store: SelfModelStore
    profile: dict[str, Any]
    self_model: dict[str, Any]


@dataclass(slots=True)
class WorldComponents:
    snapshot_store: SnapshotStore
    model: WorldModelGraph
    context_builder: ContextBuilder
    proactive_state: dict[str, Any]


@dataclass(slots=True)
class AgentComponents:
    registry: AgentRegistry
    router: AgentRouter
    orchestrator: AgentOrchestrator


@dataclass(slots=True)
class ExecutorComponents:
    audit_log: ExecutorAuditLog
    executors: dict[str, Any]


def ensure_runtime_dirs(settings: Settings) -> None:
    for path in (
        settings.data_dir,
        settings.log_dir,
        settings.template_dir,
        settings.static_dir,
        settings.snapshot_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def create_initial_state() -> dict[str, Any]:
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


def load_constitution(settings: Settings) -> dict[str, Any]:
    """Load the canonical safety rules, failing closed on malformed YAML."""
    path = settings.base_dir / "constitution.yaml"
    if not path.exists():
        logger.warning("constitution.yaml not found; using defaults")
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        logger.info("constitution.yaml loaded (version=%s)", data.get("version", "?"))
        return data
    except yaml.YAMLError as exc:
        logger.critical("constitution.yaml is malformed; refusing to start")
        raise RuntimeError(f"Failed to parse constitution.yaml: {exc}") from exc
    except OSError as exc:
        logger.critical("constitution.yaml could not be read: %s", exc)
        raise RuntimeError(f"Failed to load constitution.yaml: {exc}") from exc


def build_memory(settings: Settings, state: dict[str, Any]) -> MemoryComponents:
    store = _build_store(settings)
    state["db_ready"] = True

    embedding_service, vector_store = _build_vector_search(settings)
    tiered = TieredMemoryManager(
        store,
        config={
            "S1_session": {"max_entries": 200, "ttl_minutes": 30},
            "S2_working": {"max_entries": 500, "ttl_hours": 72},
            "S3_long_term": {"max_entries": 10000},
        },
        embedding_service=embedding_service,
        vector_store=vector_store,
    )

    repository = MemoryRepository(store)
    governor = MemoryGovernor(
        repository,
        tiered_memory=tiered,
        llm=_memory_compactor_llm(),
    )
    tiered._governor = governor

    _schedule_reindex_if_stale(store, tiered, embedding_service, vector_store)
    restored = tiered.s1.restore_from_s2(tiered.s2)
    if restored:
        logger.info("restored %d session entries from S2", restored)

    return MemoryComponents(
        store=store,
        api=MemoryAPI(store),
        repository=repository,
        governor=governor,
        tiered=tiered,
        embedding_service=embedding_service,
        vector_store=vector_store,
    )


def build_identity(settings: Settings, state: dict[str, Any]) -> IdentityComponents:
    profile_store = ProfileStore(settings.profile_path)
    self_model_store = SelfModelStore(settings.self_model_path)
    result = IdentityComponents(
        profile_store=profile_store,
        self_model_store=self_model_store,
        profile=profile_store.load_or_init(),
        self_model=self_model_store.load_or_init(),
    )
    state.update({
        "profile_ready": True,
        "persona_ready": True,
        "self_model_ready": True,
    })
    logger.info("profile and self-model loaded")
    return result


def build_world(
    settings: Settings,
    state: dict[str, Any],
    tiered_memory: TieredMemoryManager,
    persona_service: PersonaService,
) -> WorldComponents:
    snapshot_store = SnapshotStore(
        snapshot_dir=settings.snapshot_dir,
        latest_snapshot_path=settings.latest_snapshot_path,
    )
    snapshot = snapshot_store.load_latest()
    if snapshot and isinstance(snapshot, dict) and "world_model" in snapshot:
        model = WorldModelGraph.from_dict(snapshot["world_model"])
        source = "snapshot"
    else:
        model = WorldModelGraph()
        source = "S4"
    model.load_from_store(tiered_memory.s4)
    logger.info(
        "world model loaded from %s (entities=%d, edges=%d)",
        source,
        model.entity_count,
        model.edge_count,
    )
    state["snapshot_ready"] = True

    proactive_state = snapshot.get("proactive_state", {}) if snapshot else {}
    proactive_state.setdefault("last_user_message_ts", None)
    proactive_state.setdefault("last_reminder_ts", None)
    return WorldComponents(
        snapshot_store=snapshot_store,
        model=model,
        context_builder=ContextBuilder(
            persona_service=persona_service,
            tiered_memory=tiered_memory,
            world_model=model,
        ),
        proactive_state=proactive_state,
    )


def build_agents(
    state: dict[str, Any],
    tool_registry: Any = None,
    base_dir: Path | None = None,
    llm_max_retries: int = 2,
) -> AgentComponents:
    registry = AgentRegistry()
    for agent in (
        ChatAgent(
            tool_registry=tool_registry,
            llm_max_retries=llm_max_retries,
        ),
        DocsAgent(),
        SearchAgent(base_dir=base_dir),
        CodingAgent(base_dir=base_dir),
    ):
        registry.register(agent)
    state["registry_ready"] = True
    state["agents"] = registry.list_agents()
    logger.info("agent registry initialized with %d agents", len(state["agents"]))
    return AgentComponents(
        registry=registry,
        router=AgentRouter(registry),
        orchestrator=AgentOrchestrator(registry),
    )


def build_policy(settings: Settings, constitution: dict[str, Any]) -> PolicyEngine:
    policy_config: dict[str, Any] = {}
    for section in ("state_machine", "priority_system"):
        if constitution.get(section):
            policy_config[section] = constitution[section]

    override_path = settings.base_dir / "config" / "policy.yaml"
    if override_path.exists():
        with override_path.open("r", encoding="utf-8") as fh:
            _deep_merge(policy_config, yaml.safe_load(fh) or {})

    engine = PolicyEngine(policy_config)
    engine.transition("user_command")
    engine.transition("command_completed")
    logger.info("policy engine initialized (state=%s)", engine.get_state())
    return engine


def build_executors(
    settings: Settings,
    store: BaseStorageAdapter,
    constitution: dict[str, Any],
) -> ExecutorComponents:
    config = _executor_config(settings, constitution)
    audit_log = ExecutorAuditLog(store)
    executors: dict[str, Any] = {
        "file": FileExecutor(audit_log, config),
        "code": CodeExecutor(audit_log, config),
        "browser": BrowserExecutor(audit_log, config),
        "api": APIExecutor(audit_log, config),
        "comms": CommsExecutor(audit_log, config),
    }
    if is_playwright_available():
        try:
            executors["playwright_browser"] = PlaywrightBrowserExecutor(audit_log, config)
            logger.info("playwright browser executor enabled")
        except Exception as exc:
            logger.warning("playwright browser executor init failed: %s", exc)
    logger.info("executor framework initialized (%d executors)", len(executors))
    return ExecutorComponents(audit_log=audit_log, executors=executors)


def build_snapshot_payload(
    world_model: WorldModelGraph,
    profile: dict[str, Any],
    self_model: dict[str, Any],
    proactive_state: dict[str, Any],
    tiered_memory: TieredMemoryManager | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": "0.1",
        "world_model": world_model.to_dict(),
        "profile": profile,
        "self_model": self_model,
        "proactive_state": proactive_state,
    }
    if tiered_memory is None:
        return payload

    try:
        payload["memory"] = {
            "s1_session": [
                {"key": key, "value": value}
                for key, value in tiered_memory.s1.list_all()[:20]
            ],
            "s2_working": [
                {
                    "id": row.get("id"),
                    "content": row.get("content", "")[:200],
                    "source": row.get("source", ""),
                    "created_at": row.get("created_at", ""),
                }
                for row in tiered_memory.s2.list_recent(limit=20)
            ],
            "s3_long_term": [
                {
                    "id": row.get("id"),
                    "content": row.get("content", "")[:200],
                    "category": row.get("category", ""),
                    "importance": row.get("importance", 0),
                }
                for row in tiered_memory.s3.search("", limit=20)
            ],
        }
    except Exception:
        logger.debug("memory snapshot unavailable", exc_info=True)
    return payload


def start_github_poller(
    event_bus: EventBus,
    token: str,
    poll_repos: str,
    interval_sec: int,
) -> Any | None:
    if not token or not poll_repos:
        return None
    try:
        from connectors.github.poller import GitHubPoller

        repos = [repo.strip() for repo in poll_repos.split(",") if repo.strip()]
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


def _build_store(settings: Settings) -> BaseStorageAdapter:
    if settings.storage_backend == "postgresql":
        from memory.postgres_store import PostgresStore

        store = PostgresStore(database_url=settings.database_url) if settings.database_url else PostgresStore()
        store.init_db()
        logger.info("database initialized (PostgreSQL)")
        return store

    store = SQLiteStore(settings.db_path)
    store.init_db()
    logger.info("database initialized at %s", settings.db_path)
    return store


def _build_vector_search(settings: Settings) -> tuple[Any, Any]:
    if settings.embedding_provider != "local":
        return None, None
    if find_spec("sentence_transformers") is None or find_spec("faiss") is None:
        logger.warning("sentence-transformers or faiss not installed; vector search disabled")
        return None, None
    try:
        from memory.embedding_service import EmbeddingService
        from memory.vector_store import VectorStore

        service = EmbeddingService(model_name=settings.embedding_model_name)
        store = VectorStore(dim=service.dim, index_path=settings.vector_index_path)
        store.load()
        logger.info("vector store loaded (%d vectors)", store.size())
        return service, store
    except Exception:
        logger.exception("failed to initialize vector search; disabling")
        return None, None


def _memory_compactor_llm() -> Any:
    try:
        from core.llm_adapter import MockLLM, get_llm

        candidate = get_llm()
        if not isinstance(candidate, MockLLM):
            logger.info("memory compactor upgraded to LLM summarization")
            return candidate
    except Exception:
        logger.debug("memory compactor LLM unavailable", exc_info=True)
    return None


def _schedule_reindex_if_stale(
    store: BaseStorageAdapter,
    tiered: TieredMemoryManager,
    embedding_service: Any,
    vector_store: Any,
) -> None:
    if vector_store is None or embedding_service is None:
        return
    try:
        rows = store.fetchall(
            "SELECT COUNT(*) as cnt FROM long_term_memory WHERE status='active'",
            (),
        )
        active_count = rows[0]["cnt"] if rows else 0
        if active_count <= 0 or abs(active_count - vector_store.size()) <= active_count * 0.1:
            return
        from memory.reindex_job import reindex_all

        logger.info(
            "vector index stale (%d DB vs %d indexed); scheduling reindex",
            active_count,
            vector_store.size(),
        )
        threading.Thread(
            target=reindex_all,
            args=(embedding_service, vector_store, tiered.s3),
            daemon=True,
            name="eva-reindex",
        ).start()
    except Exception:
        logger.exception("vector staleness check failed")


def _executor_config(settings: Settings, constitution: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    boundaries = constitution.get("boundaries", {})
    fs_boundary = boundaries.get("filesystem", {})
    compute_boundary = boundaries.get("compute", {})
    network_boundary = boundaries.get("network", {})

    if fs_boundary:
        file_config = config.setdefault("executors", {}).setdefault("file", {})
        limits = file_config.setdefault("limits", {})
        if fs_boundary.get("max_file_size_mb"):
            limits["max_file_size_mb"] = fs_boundary["max_file_size_mb"]
        file_config["forbidden_paths"] = list(fs_boundary.get("forbidden_paths", []))
        platform_paths = {
            "Windows": (
                "C:\\Windows",
                "C:\\Windows\\System32",
                "C:\\Program Files",
                "C:\\Program Files (x86)",
            ),
            "Darwin": ("/System", "/Library/System", "/private/etc", "/private/var"),
        }
        for path in platform_paths.get(platform.system(), ()):
            if path not in file_config["forbidden_paths"]:
                file_config["forbidden_paths"].append(path)

    if compute_boundary.get("max_process_duration_minutes"):
        code_limits = (
            config.setdefault("executors", {})
            .setdefault("code", {})
            .setdefault("limits", {})
        )
        code_limits["timeout"] = compute_boundary["max_process_duration_minutes"] * 60

    allowed_domains = network_boundary.get("allowed_domains", [])
    if allowed_domains:
        executors = config.setdefault("executors", {})
        executors.setdefault("browser", {})["allowed_domains"] = allowed_domains
        api_config = executors.setdefault("api", {})
        api_config["allowed_domains"] = allowed_domains
        api_config["allowed_ports"] = network_boundary.get("allowed_ports", [80, 443])

    override_path = settings.base_dir / "config" / "executors.yaml"
    if override_path.exists():
        with override_path.open("r", encoding="utf-8") as fh:
            _deep_merge(config, yaml.safe_load(fh) or {})

    file_config = config.setdefault("executors", {}).setdefault("file", {})
    allowed_paths = file_config.setdefault("allowed_paths", [])
    for path in (settings.base_dir, settings.data_dir, settings.log_dir):
        resolved = str(path.resolve())
        if resolved not in allowed_paths:
            allowed_paths.append(resolved)
    return config


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
