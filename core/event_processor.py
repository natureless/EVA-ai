"""Scheduler-independent event execution service."""

import logging
from copy import deepcopy
import sqlite3
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any
from uuid import uuid4

from agent_os.orchestrator import AgentOrchestrator
from agent_os.router import AgentRouter
from agent_os.agent_task import AGENT_EXECUTOR_MAP
from agents.base_agent import AgentResult
from core.context_builder import ContextBuilder
from core.entity_extractor import entity_extractor
from core.planner import Planner
from core.policy_engine import PolicyEngine, Verdict
from core.prediction import PredictionTracker
from core.proactive_engine import ProactiveEngine
from event.event_bus import EventBus
from event.event_schema import Event, TraceRecord
from memory.memory_api import MemoryAPI
from memory.memory_governor import MemoryGovernor
from core.memory_ingestor import MemoryIngestor
from memory.tiered_store import TieredMemoryManager
from memory.provenance import EpistemicStatus, provenance
from persona.self_model_store import SelfModelStore
from runtime.result_registry import ResultRegistry
from runtime.request_persistence import ActionContextError
from runtime.agent_worker import (
    AgentWorkerBackend,
    ThreadAgentWorkerBackend,
    worker_is_idle,
)
from world.world_model import WorldModelGraph


class EventProcessor:
    """Execute one event without owning a consumer thread or scheduler.

    Entry/action policy, routing, reviewed replies, provenance, memory and world
    updates retain one shared implementation. Queue consumption and task_done
    belong to the selected scheduler. Dependencies are explicitly injected;
    this service never imports the application or experimental runtime layer.

    Concurrent callers share the existing world/memory services. Policy state
    transitions and processor counters are locked; the legacy system_state
    projection retains its existing last-write-wins semantics.
    """

    def __init__(
        self,
        event_bus: EventBus,
        memory_api: MemoryAPI,
        memory_governor: MemoryGovernor | None,
        planner: Planner,
        agent_router: AgentRouter,
        orchestrator: AgentOrchestrator,
        result_registry: ResultRegistry,
        proactive_engine: ProactiveEngine,
        proactive_state: dict[str, Any],
        world_model: WorldModelGraph,
        system_state: dict[str, Any],
        result_ttl_sec: float = 60.0,
        context_builder: ContextBuilder | None = None,
        prediction_tracker: PredictionTracker | None = None,
        self_model_store: SelfModelStore | None = None,
        self_model: dict[str, Any] | None = None,
        policy_engine: PolicyEngine | None = None,
        tiered_memory: TieredMemoryManager | None = None,
        executors: dict[str, Any] | None = None,
        ws_manager: Any = None,
        agent_worker_backend: AgentWorkerBackend | None = None,
        episode_recorder: Any = None,
    ) -> None:
        self.event_bus = event_bus
        self.memory_api = memory_api
        self.memory_governor = memory_governor
        self.planner = planner
        self.agent_router = agent_router
        self.orchestrator = orchestrator
        self.result_registry = result_registry
        self.proactive_engine = proactive_engine
        self.proactive_state = proactive_state
        self.world_model = world_model
        self.system_state = system_state
        self.result_ttl_sec = result_ttl_sec
        self.episode_recorder = episode_recorder
        self.context_builder = context_builder
        self.prediction_tracker = prediction_tracker
        self.self_model_store = self_model_store
        self.self_model = self_model
        self.policy_engine = policy_engine
        self.tiered_memory = tiered_memory
        self._executors: dict[str, Any] = executors or {}
        self._ws_manager = ws_manager
        self._memory_ingestor = MemoryIngestor(memory_governor, self_model)
        self._owns_agent_worker = agent_worker_backend is None
        self._agent_worker = agent_worker_backend or ThreadAgentWorkerBackend(
            orchestrator
        )

        self._stop_event = threading.Event()
        self._activity_lock = threading.RLock()
        self._active_events = 0
        self._closed = False
        self._consecutive_errors_lock = threading.Lock()
        self._consecutive_errors = 0
        self._policy_lock = threading.Lock()  # serialize policy state transitions
        self._TRANSIENT_SQLITE = {
            "database is locked",
            "database schema is locked",
            "busy",
        }
        self._total_processed = 0
        self._total_errors = 0

    def request_stop(self) -> None:
        """Reject new work and signal cooperative cancellation to agent calls."""
        with self._activity_lock:
            self._stop_event.set()
            receipts = self.result_registry.reject_waiting()
        for payload in receipts:
            self._broadcast_receipt(payload)

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def reset_stop(self) -> None:
        """Prepare an idle service for a scheduler start."""
        with self._activity_lock:
            if self._closed:
                raise RuntimeError("Event processor has been closed")
            if not self.is_idle:
                raise RuntimeError("Cannot restart while event execution is active")
            self._stop_event.clear()

    @property
    def is_idle(self) -> bool:
        """Include real backend occupancy after a timed-out caller returns."""
        with self._activity_lock:
            if self._active_events:
                return False
        return worker_is_idle(self._agent_worker)

    def shutdown_owned_backend(self) -> bool:
        """Close only internally created workers, once all execution is idle."""
        with self._activity_lock:
            if not self.is_idle:
                return False
            if self._owns_agent_worker and not self._closed:
                self._stop_event.set()
                self._agent_worker.shutdown()
                self._closed = True
            return True

    @property
    def stats(self) -> dict[str, Any]:
        with self._activity_lock:
            active_events = self._active_events
            stopping = self._stop_event.is_set()
        with self._consecutive_errors_lock:
            counts = {
                "total_processed": self._total_processed,
                "total_errors": self._total_errors,
                "consecutive_errors": self._consecutive_errors,
            }
        return {
            **counts,
            "active_events": active_events,
            "stopping": stopping,
            "agent_worker": self._agent_worker.stats,
        }

    # ── agent execution ─────────────────────────────────────

    def _execute_agent(
        self,
        selected_agent: str,
        task: Any,
        agent_exe: Any,
        token_manager: Any,
        agent_tok: str,
    ) -> tuple[AgentResult, int]:
        """Delegate an agent call to the configured worker backend."""
        return self._agent_worker.execute(
            selected_agent,
            task,
            executor=agent_exe,
            token_manager=token_manager,
            token_id=agent_tok,
            stop_event=self._stop_event,
        )

    def _execute_agent_stream(
        self,
        selected_agent: str,
        task: Any,
        correlation_id: str,
    ) -> tuple[AgentResult, int]:
        """Run the agent with per-token WS broadcast, polling for stop events.

        Each token is broadcast via ``chat_token`` channel so SSE/WS clients
        receive incremental output. The final result is NOT broadcast here —
        the caller handles ``chat_reply`` broadcast.
        """
        ws = self._ws_manager

        def _on_token(token: str) -> None:
            if ws:
                ws.broadcast_sync(
                    "chat_token",
                    {
                        "task_id": correlation_id,
                        "token": token,
                    },
                )

        return self._agent_worker.execute_stream(
            selected_agent,
            task,
            _on_token,
            stop_event=self._stop_event,
        )

    def process_event(
        self,
        event: Event,
        *,
        cognitive_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Process an already-consumed event using the stable execution boundary.

        The caller owns ``consume``/``task_done``. This method never starts a
        consumer. Any scheduler can call this service directly.
        ``cognitive_context`` is supplied by that scheduler, never by payload.
        """
        # Callers may invoke the processor without passing through EventBus.
        # Validate before policy, world, model, memory or tool effects.
        try:
            Event.model_validate(event.model_dump())
        except ValueError:
            return self.reject_event(event, "invalid_event_contract")
        with self._activity_lock:
            if self._stop_event.is_set() or self._closed:
                return self.reject_event(event, "runtime_stopping")
            if event.correlation_id:
                claimed = self.result_registry.begin(
                    event.correlation_id, event_id=event.id, event=event
                )
                if claimed != "started":
                    previous = self.result_registry.peek(event.correlation_id)
                    if previous is not None:
                        return previous
                    # Do not fulfill a duplicate against an active original.
                    return {
                        "task_id": event.correlation_id,
                        "ok": False,
                        "error": "request_already_running"
                        if claimed == "running"
                        else "result_unavailable",
                    }
            self._active_events += 1
        logger = logging.getLogger("eva.cognition_loop")
        worker_name = threading.current_thread().name
        start = time.perf_counter()
        loop_id = f"loop_{uuid4().hex[:8]}"
        episode_started = False
        receipt = None
        try:
            if self.episode_recorder is not None:
                episode_started = self.episode_recorder.begin(
                    event, loop_id, self.world_model.to_dict()
                )
                if not episode_started:
                    return self._deliver_failure(
                        event, loop_id, "episode_already_recorded"
                    )
            receipt = self._process_one(
                event,
                loop_id,
                start,
                logger,
                cognitive_context=cognitive_context,
            )
        except ActionContextError as exc:
            reason = (
                "action_context_outcome_unknown"
                if str(exc) == "agent observation commit unavailable"
                else "action_context_unavailable"
            )
            receipt = self._deliver_failure(event, loop_id, reason)
            return receipt
        except sqlite3.OperationalError as exc:
            is_transient = any(
                tag in str(exc).lower() for tag in self._TRANSIENT_SQLITE
            )
            if is_transient:
                logger.warning("[%s] transient sqlite error: %s", worker_name, exc)
                self._increment_errors()
                with self._consecutive_errors_lock:
                    quarantine = self._consecutive_errors >= 10
                    if quarantine:
                        self._consecutive_errors = 0
                if quarantine:
                    self._maybe_quarantine("transient_sqlite")
            else:
                logger.exception("[%s] sqlite error", worker_name)
                self._maybe_quarantine("sqlite_error")
            receipt = self._deliver_failure(event, loop_id, "storage_error")
            return receipt
        except Exception:
            logger.exception("[%s] cognition loop error", worker_name)
            self._increment_errors()
            with self._consecutive_errors_lock:
                quarantine = self._consecutive_errors >= 3
                if quarantine:
                    self._consecutive_errors = 0
            if quarantine:
                self._maybe_quarantine("consecutive_errors")
            receipt = self._deliver_failure(event, loop_id, "cognition_error")
            return receipt
        else:
            with self._consecutive_errors_lock:
                self._consecutive_errors = 0
            return receipt
        finally:
            try:
                if event.correlation_id:
                    self.result_registry.end_processing(
                        event.correlation_id,
                        event_id=event.id,
                        returned_ok=receipt.get("ok")
                        if isinstance(receipt, dict) and type(receipt.get("ok")) is bool
                        else None,
                    )
                if episode_started:
                    # Retry only audit persistence using the already canonical receipt.
                    canonical = (
                        self.result_registry.peek(event.correlation_id)
                        if event.correlation_id
                        else receipt
                    )
                    if canonical is not None:
                        try:
                            self.episode_recorder.record_receipt(canonical)
                        except Exception:
                            logger.error(
                                "Episode finalization or receipt delivery failed; durable records retained"
                            )
                self.result_registry.cleanup(ttl_sec=self.result_ttl_sec)
                self.system_state["pending_events"] = self.event_bus.size()
                self.system_state["pending_results"] = self.result_registry.size()
            finally:
                with self._activity_lock:
                    self._active_events -= 1

    def reject_event(self, event: Event, reason: str) -> dict[str, Any]:
        """Deliver a scheduler rejection through the existing result channels."""
        return self._deliver_failure(event, f"loop_{uuid4().hex[:8]}", reason)

    # ── event processing ────────────────────────────────────

    def _process_one(
        self,
        event: Event,
        loop_id: str,
        start: float,
        logger: logging.Logger,
        *,
        cognitive_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Process a single event through the full cognition pipeline.

        Scheduler lifecycle and queue acknowledgement remain with the caller.
        """
        # ── policy checkpoint A: event entry guard ──
        if self.policy_engine:
            with self._policy_lock:
                policy_decision = self.policy_engine.evaluate(
                    event.type,
                    context={"source": event.source, "task_id": loop_id},
                )
            if policy_decision.verdict == Verdict.QUARANTINE:
                logger.critical("policy: quarantine triggered — blocking event")
                self.system_state["policy_state"] = self.policy_engine.get_state()
                if self._ws_manager:
                    self._ws_manager.broadcast_sync(
                        "policy_state", self.system_state["policy_state"]
                    )
                return self._deliver_failure(event, loop_id, "policy_quarantine")
            if policy_decision.verdict == Verdict.DENY:
                logger.warning("policy: event denied — %s", policy_decision.reason)
                self.system_state["policy_state"] = self.policy_engine.get_state()
                if self._ws_manager:
                    self._ws_manager.broadcast_sync(
                        "policy_state", self.system_state["policy_state"]
                    )
                return self._deliver_failure(event, loop_id, "policy_denied")

        self.memory_api.append_event(event)
        plan = self.planner.plan(event)

        result_summary = "no action"
        selected_agent = plan.agent
        reply = ""
        result = AgentResult(
            ok=True, agent=selected_agent, content="", summary="no action"
        )
        agent_duration_ms = 0
        self_model_delta = 0.0
        memory_write_ids: dict[str, str] = {}

        now_ts = time.time()

        if event.type == "user_message":
            user_text = str(event.payload.get("text", ""))
            # Keep mode request-local across all planner routes and worker backends.
            plan.task.payload["mode"] = event.payload.get("mode", "normal")
            self.world_model.apply_user_message(user_text)
            self.proactive_state["last_user_message_ts"] = now_ts

            user_memory = self._memory_ingestor.ingest_user_message(
                text=user_text,
                event_id=event.id,
                source=event.source,
                active_tasks=self.world_model.active_tasks,
                remember=event.payload.get("remember") is True,
            )
            if user_memory is not None:
                memory_write_ids = user_memory.metadata.get("tier_ids", {})

            if self.context_builder:
                user_id = str(event.payload.get("user_id", "default"))
                ctx = self.context_builder.build(
                    user_id=user_id,
                    text=user_text,
                )
                # inject context into task for agent consumption
                plan.task.payload["context"] = ctx
                ctx["memory_write"] = {
                    "requested": event.payload.get("remember") is True,
                    "long_term_id": memory_write_ids.get("s3"),
                }
                self.system_state["last_context_summary"] = {
                    "persona_loaded": bool(ctx.get("persona")),
                    "memory_items": len(ctx.get("memories") or []),
                    "active_tasks": len(ctx.get("active_tasks", [])),
                    "entities": len(ctx.get("recent_entities", [])),
                    "summary": ctx.get("context_summary", ""),
                }

        if cognitive_context is not None:
            # Replace any caller-supplied field with the scheduler's own view.
            ctx = plan.task.payload.setdefault("context", {})
            ctx["minimal_brain"] = deepcopy(cognitive_context)

        if event.type == "maintenance":
            decision = self.proactive_engine.evaluate_stagnation(
                world_model=self.world_model.to_dict(),
                proactive_state=self.proactive_state,
                now_ts=now_ts,
            )
            self.system_state["last_proactive_reason"] = decision.reason
            logger.info("proactive decision: %s", decision.reason)

            if self.memory_governor:
                stats = self.memory_governor.maintenance()
                self.system_state["last_memory_governor"] = stats

            if decision.should_trigger:
                reminder_event = Event(
                    type="reminder_trigger",
                    source="proactive_engine",
                    payload={
                        "message": self.proactive_engine.build_reminder_message(
                            focus=decision.payload["focus"],
                            idle_sec=decision.payload["idle_sec"],
                        ),
                        "reason": decision.reason,
                        "focus": decision.payload["focus"],
                        "idle_sec": decision.payload["idle_sec"],
                        "triggered_at": decision.payload["triggered_at"],
                    },
                )
                self.event_bus.publish(reminder_event)

        if plan.decision == "act":
            # ── policy checkpoint B: priority + token check ──
            blocked = False
            if self.policy_engine:
                with self._policy_lock:
                    pol = self.policy_engine.evaluate(
                        event.type,
                        context={
                            "source": event.source,
                            "task_id": loop_id,
                            "executor_type": plan.agent,
                        },
                    )
                if pol.verdict == Verdict.DENY:
                    logger.warning("policy: action denied — %s", pol.reason)
                    result_summary = f"blocked: {pol.reason}"
                    reply = f"[policy] {pol.reason}"
                    selected_agent = plan.agent
                    blocked = True
                    result = AgentResult(
                        ok=False,
                        agent=selected_agent,
                        content=reply,
                        summary=result_summary,
                        meta={"error": "policy_denied"},
                    )

            if not blocked:
                plan.task.trace_context = {
                    "task_id": loop_id,
                    "trace_id": loop_id,
                    "loop_id": loop_id,
                    "correlation_id": event.correlation_id or "",
                    "causation_id": event.id,
                }
                selected_agent = self.agent_router.route(plan.agent, plan.task)
                logger.debug(
                    "routing: plan.agent=%s selected_agent=%s task.kind=%s",
                    plan.agent,
                    selected_agent,
                    plan.task.kind,
                )

                # ── streaming mode: chat_agent only ──
                stream_requested = event.payload.get("stream") and event.correlation_id
                agent_needs_executor = (
                    AGENT_EXECUTOR_MAP.get(selected_agent) and self._executors
                )

                if stream_requested and not agent_needs_executor:
                    assert event.correlation_id is not None
                    result, agent_duration_ms = self._invoke_prepared_agent(
                        event,
                        loop_id,
                        selected_agent,
                        plan.task,
                        lambda: self._execute_agent_stream(
                            selected_agent, plan.task, event.correlation_id
                        ),
                    )
                    reply = result.content
                else:
                    # ── agent → executor routing ──────────
                    agent_exe = None
                    agent_tok = ""
                    if self._executors:
                        exe_type = AGENT_EXECUTOR_MAP.get(selected_agent)
                        agent_exe = self._executors.get(exe_type) if exe_type else None
                        if agent_exe and self.policy_engine:
                            with self._policy_lock:
                                tm = self.policy_engine.token_manager
                                token = tm.issue(
                                    loop_id,
                                    exe_type or "agent",
                                    scope=[exe_type] if exe_type else [],
                                    ttl_seconds=300,
                                    budget_tokens=10,
                                )
                                agent_tok = token.token_id

                    result, agent_duration_ms = self._invoke_prepared_agent(
                        event,
                        loop_id,
                        selected_agent,
                        plan.task,
                        lambda: self._execute_agent(
                            selected_agent,
                            plan.task,
                            agent_exe,
                            self.policy_engine.token_manager
                            if self.policy_engine
                            else None,
                            agent_tok,
                        ),
                    )
                    reply = result.content

                if self.episode_recorder is not None:
                    self.episode_recorder.action_finished(event.id, loop_id, result)

            # ── self-model feedback: prediction error + state recording ──
            if not blocked and result.ok:
                prev_focus = self.world_model.focus

                self.world_model.apply_agent_result(
                    reply=reply,
                    selected_agent=selected_agent,
                    loop_id=loop_id,
                )

                # compute prediction error if tracker is wired
                prediction_error = 0.0
                if self.prediction_tracker and prev_focus:
                    expected = (
                        f"agent {selected_agent} responds about {prev_focus[:60]}"
                    )
                    actual = reply[:200] if reply else ""
                    rec = self.prediction_tracker.record(
                        focus=prev_focus, expected=expected, actual=actual
                    )
                    prediction_error = rec.error

                # assess self-model delta & record state change
                self_model_delta = 0.0
                if self.self_model_store and self.self_model is not None:
                    self.self_model_store.record_state_change(
                        self.self_model,
                        change_type="agent_execution",
                        detail=f"{selected_agent}: {result.summary}",
                        focus=self.world_model.focus,
                        loop_id=loop_id,
                    )
                    if prediction_error > 0:
                        self.self_model_store.record_prediction_error(
                            self.self_model,
                            error=prediction_error,
                            focus=self.world_model.focus,
                        )
                    self_model_delta = prediction_error

                    perturbation_threshold = 0.6
                    if prediction_error > perturbation_threshold:
                        self.self_model_store.record_perturbation(
                            self.self_model,
                            cause=f"high prediction error ({prediction_error:.2f}) "
                            f"on agent {selected_agent}",
                            delta_magnitude=prediction_error,
                            affected_fields=["world_model.focus", "prediction"],
                            loop_id=loop_id,
                        )

            if event.type == "reminder_trigger" and result.ok:
                self.world_model.apply_reminder()
                self.proactive_state["last_reminder_ts"] = now_ts

            result_summary = result.summary
            self.memory_api.write_episodic_memory(
                event_type=event.type,
                summary=f"{selected_agent}: {result_summary}",
                payload={
                    "event_id": event.id,
                    "correlation_id": event.correlation_id,
                    "task_kind": plan.task.kind,
                    "task_payload": plan.task.payload,
                    "reply": reply,
                    "agent_meta": result.meta,
                    "ok": result.ok,
                    "agent_duration_ms": agent_duration_ms,
                },
                importance=0.7 if event.type == "reminder_trigger" else 0.6,
            )

            # ── tiered memory (S1-S3) ─────────────────
            if self.tiered_memory and not blocked and result.ok:
                imp = 0.7 if event.type == "reminder_trigger" else 0.6
                self.tiered_memory.ingest(
                    reply if reply else result_summary,
                    importance=imp,
                    source=selected_agent,
                    category=event.type,
                    tags=[selected_agent, event.type],
                    source_event_id=event.id,
                    self_model_delta=self_model_delta,
                    prediction_error=prediction_error,
                    origin=provenance(
                        EpistemicStatus.ASSISTANT_INFERENCE,
                        source=selected_agent,
                        source_event_id=event.id,
                    ),
                )

            # ── world model: entity extraction → graph → S4 ──
            if not blocked and result.ok and reply and self.world_model:
                # Reply extraction produces inferences, regardless of labels or
                # confidence values the model/extractor places in its payload.
                graph_origin = provenance(
                    EpistemicStatus.ASSISTANT_INFERENCE,
                    source=selected_agent,
                    source_event_id=event.id,
                )
                try:
                    future = self._agent_worker.submit(
                        entity_extractor.extract_from_reply, reply
                    )
                    entities, relations = future.result(timeout=5.0)
                except FutureTimeoutError:
                    entities, relations = [], []
                for e in entities:
                    self.world_model.upsert_entity(
                        e["type"],
                        e["name"],
                        e.get("properties", {}),
                        origin=graph_origin,
                    )
                    # broadcast entity creation
                    if self._ws_manager:
                        self._ws_manager.broadcast_sync(
                            "entity_created",
                            {
                                "type": e["type"],
                                "name": e["name"],
                                "properties": e.get("properties", {}),
                                "provenance": graph_origin,
                            },
                        )
                for r in relations:
                    self.world_model.link(
                        r["source"],
                        r["target"],
                        r["relation"],
                        weight=r.get("weight", 1.0),
                        origin=graph_origin,
                    )
                if self.tiered_memory:
                    self.world_model.flush(self.tiered_memory.s4)

        duration_ms = int((time.perf_counter() - start) * 1000)

        trace = TraceRecord(
            loop_id=loop_id,
            event_type=event.type,
            decision=plan.decision,
            agent=selected_agent,
            result_summary=result_summary,
            duration_ms=duration_ms,
        )
        self.memory_api.write_trace(trace)

        self.system_state["focus"] = self.world_model.focus
        self.system_state["mode"] = self.world_model.mode
        self.system_state["active_tasks"] = self.world_model.active_tasks
        self.system_state["last_reply"] = self.world_model.last_reply
        self.system_state["last_selected_agent"] = self.world_model.last_selected_agent
        self.system_state["last_loop_id"] = self.world_model.last_loop_id
        self.system_state["last_loop_at"] = self.world_model.last_loop_at

        # ── self-model stability → system_state ──
        if self.self_model is not None:
            metrics = self.self_model.get("stability_metrics", {})
            self.system_state["stability_score"] = metrics.get("stability_score", 1.0)
            self.system_state["mean_prediction_error"] = metrics.get(
                "mean_prediction_error", 0.0
            )
            self.system_state["total_perturbations"] = metrics.get(
                "total_perturbations", 0
            )

        result.meta["memory_write_ids"] = memory_write_ids
        receipt = self._deliver_result(event, loop_id, result, duration_ms)

        with self._consecutive_errors_lock:
            self._total_processed += 1

        logger.info(
            "loop event=%s decision=%s agent=%s duration_ms=%s",
            event.type,
            plan.decision,
            selected_agent,
            duration_ms,
        )
        return receipt

    # ── helpers ──────────────────────────────────────────────

    def _deliver_result(
        self, event: Event, loop_id: str, result: AgentResult, duration_ms: int
    ) -> dict[str, Any]:
        payload = {
            "task_id": event.correlation_id,
            "ok": result.ok,
            "reply": result.content,
            "selected_agent": result.agent,
            "mode": event.payload.get("mode", "normal"),
            "mode_info": result.meta.get("mode_info"),
            "loop_id": loop_id,
            "event_id": event.id,
            "event_type": event.type,
            "duration_ms": duration_ms,
            "error": result.meta.get("error", result.meta.get("status"))
            if not result.ok
            else None,
            "review": result.meta.get(
                "review",
                {"status": "not_assessed", "passed": None, "fact_verified": False},
            ),
            "memory_write_ids": result.meta.get("memory_write_ids", {}),
        }
        uncertain = {
            "action_context_outcome_unknown",
            "timeout",
            "cancelled",
            "execution_timeout",
            "heartbeat_timeout",
            "worker_crash",
            "worker_crashed",
            "worker_lost",
            "protocol_error",
        }
        rejected = {
            "action_context_unavailable",
            "runtime_stopping",
            "runtime_stopped",
            "kernel_stopped",
            "kernel_failed",
            "cognitive_queue_full",
            "duplicate_event_id",
            "policy_denied",
            "policy_quarantine",
        }
        error = payload["error"] if isinstance(payload["error"], str) else ""
        payload["terminal_state"] = (
            "succeeded"
            if result.ok
            else "outcome_unknown"
            if error in uncertain
            else "rejected"
            if error in rejected
            else "expired"
            if error == "cognitive_queue_timeout"
            else "failed"
        )
        if payload["terminal_state"] == "outcome_unknown":
            payload["execution_state"] = "outcome_unknown"
            payload["reply"] = "请求等待已结束，执行结果尚不确定。"
        if self.episode_recorder is not None:
            try:
                self.episode_recorder.checkpoint(
                    event.id, loop_id, self.world_model.to_dict()
                )
            except Exception:
                logging.getLogger("eva.cognition_loop").error(
                    "Episode checkpoint failed; snapshot remains unknown"
                )
        if event.correlation_id:
            if not self.result_registry.fulfill(event.correlation_id, payload):
                return self.result_registry.peek(event.correlation_id) or payload
            self._broadcast_receipt(payload)
        return payload

    def _broadcast_receipt(self, payload: dict[str, Any]) -> None:
        if self._ws_manager:
            try:
                self._ws_manager.broadcast_sync("chat_reply", payload)
            except Exception:
                # Delivery failure must not replace the already stored result.
                logging.getLogger("eva.cognition_loop").exception(
                    "receipt broadcast failed; polling remains available"
                )

    def _deliver_failure(
        self, event: Event, loop_id: str, reason: str
    ) -> dict[str, Any]:
        result = AgentResult(
            ok=False,
            agent="system",
            content=f"本次请求未完成：{reason}。",
            summary=reason,
            meta={"error": reason},
        )
        return self._deliver_result(event, loop_id, result, 0)

    def _invoke_prepared_agent(self, event, loop_id, agent, task, invoke):
        # Commit the exact task/context evidence before entering the real worker.
        binding = self.result_registry.begin_agent_action(event, agent, task)
        try:
            if self.episode_recorder is not None:
                if binding is None:
                    self.episode_recorder.action_started(event.id, loop_id, agent)
                else:
                    self.episode_recorder.action_started(
                        event.id,
                        loop_id,
                        agent,
                        intent_binding={
                            key: binding[key]
                            for key in ("action_id", "state_version", "request_hash")
                        },
                    )
            result, duration = invoke()
        except BaseException:
            self.result_registry.finish_agent_action(event, binding, returned_ok=None)
            raise
        self.result_registry.finish_agent_action(event, binding, returned_ok=result.ok)
        return result, duration

    def _increment_errors(self) -> None:
        with self._consecutive_errors_lock:
            self._consecutive_errors += 1
            self._total_errors += 1

    def _maybe_quarantine(self, reason: str) -> None:
        if self.policy_engine:
            with self._policy_lock:
                self.policy_engine.transition("violation_detected")
                self.system_state["policy_state"] = self.policy_engine.get_state()
            logging.getLogger("eva.cognition_loop").critical(
                "policy: entering quarantine due to %s", reason
            )
