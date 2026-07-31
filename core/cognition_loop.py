import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
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
from persona.self_model_store import SelfModelStore
from runtime.result_registry import ResultRegistry
from world.world_model import WorldModelGraph


class CognitionLoop:
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
        poll_timeout_sec: float = 0.5,
        result_ttl_sec: float = 60.0,
        context_builder: ContextBuilder | None = None,
        prediction_tracker: PredictionTracker | None = None,
        self_model_store: SelfModelStore | None = None,
        self_model: dict[str, Any] | None = None,
        policy_engine: PolicyEngine | None = None,
        tiered_memory: TieredMemoryManager | None = None,
        executors: dict[str, Any] | None = None,
        ws_manager: Any = None,
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
        self.poll_timeout_sec = poll_timeout_sec
        self.result_ttl_sec = result_ttl_sec
        self.context_builder = context_builder
        self.prediction_tracker = prediction_tracker
        self.self_model_store = self_model_store
        self.self_model = self_model
        self.policy_engine = policy_engine
        self.tiered_memory = tiered_memory
        self._executors: dict[str, Any] = executors or {}
        self._ws_manager = ws_manager
        self._memory_ingestor = MemoryIngestor(memory_governor, self_model)
        self._agent_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="eva-agent")
        self._agent_timeout_sec = poll_timeout_sec * 10 if poll_timeout_sec else 30.0

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._consecutive_errors = 0
        self._TRANSIENT_SQLITE = {"database is locked", "database schema is locked", "busy"}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_forever, daemon=True, name="cognition-loop")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._agent_pool.shutdown(wait=False)

    def _execute_agent(self, selected_agent: str, task: Any, agent_exe: Any, token_manager: Any, agent_tok: str) -> tuple[AgentResult, int]:
        """Run the agent in a thread pool, polling for stop events.

        Returns (AgentResult, duration_ms). On timeout or shutdown, returns an
        error result so the loop can continue without blocking forever.
        """
        future = self._agent_pool.submit(
            self.orchestrator.execute,
            selected_agent, task,
            executor=agent_exe,
            token_manager=token_manager,
            token_id=agent_tok,
        )
        poll_interval = 0.5
        elapsed = 0.0
        while not self._stop_event.is_set():
            try:
                return future.result(timeout=poll_interval)
            except FutureTimeoutError:
                elapsed += poll_interval
                if elapsed >= self._agent_timeout_sec:
                    logger = logging.getLogger("eva.cognition_loop")
                    logger.error("agent %s timed out after %.0fs", selected_agent, elapsed)
                    return (
                        AgentResult(
                            ok=False, agent=selected_agent,
                            content=f"[{selected_agent}] timed out after {elapsed:.0f}s",
                            summary=f"timeout ({elapsed:.0f}s)",
                            meta={"status": "timeout", "elapsed_sec": elapsed},
                        ),
                        int(elapsed * 1000),
                    )

        # stop_event was set — return cancelled
        logger = logging.getLogger("eva.cognition_loop")
        logger.warning("agent %s cancelled due to shutdown", selected_agent)
        return (
            AgentResult(
                ok=False, agent=selected_agent,
                content=f"[{selected_agent}] cancelled (shutdown)",
                summary="cancelled",
                meta={"status": "cancelled"},
            ),
            0,
        )

    def _execute_agent_stream(
        self, selected_agent: str, task: Any, correlation_id: str,
    ) -> tuple[AgentResult, int]:
        """Run the agent with per-token WS broadcast, polling for stop events.

        Each token is broadcast via ``chat_token`` channel so SSE/WS clients
        receive incremental output. The final result is NOT broadcast here —
        the caller handles ``chat_reply`` broadcast.
        """
        ws = self._ws_manager

        def _on_token(token: str) -> None:
            if ws:
                ws.broadcast_sync("chat_token", {
                    "task_id": correlation_id,
                    "token": token,
                })

        future = self._agent_pool.submit(
            self.orchestrator.execute_stream,
            selected_agent, task, _on_token,
        )
        poll_interval = 0.5
        elapsed = 0.0
        while not self._stop_event.is_set():
            try:
                return future.result(timeout=poll_interval)
            except FutureTimeoutError:
                elapsed += poll_interval
                if elapsed >= self._agent_timeout_sec:
                    logger = logging.getLogger("eva.cognition_loop")
                    logger.error("agent %s stream timed out after %.0fs", selected_agent, elapsed)
                    error_msg = f"[{selected_agent}] stream timed out after {elapsed:.0f}s"
                    if ws:
                        ws.broadcast_sync("chat_reply", {
                            "task_id": correlation_id,
                            "ok": False,
                            "reply": error_msg,
                            "selected_agent": selected_agent,
                            "error": "timeout",
                        })
                    return (
                        AgentResult(
                            ok=False, agent=selected_agent,
                            content=error_msg,
                            summary=f"timeout ({elapsed:.0f}s)",
                            meta={"status": "timeout", "elapsed_sec": elapsed},
                        ),
                        int(elapsed * 1000),
                    )

        logger = logging.getLogger("eva.cognition_loop")
        logger.warning("agent %s stream cancelled due to shutdown", selected_agent)
        return (
            AgentResult(
                ok=False, agent=selected_agent,
                content=f"[{selected_agent}] cancelled (shutdown)",
                summary="cancelled",
                meta={"status": "cancelled"},
            ),
            0,
        )

    def _run_forever(self) -> None:
        logger = logging.getLogger("eva.cognition_loop")
        while not self._stop_event.is_set():
            event = self.event_bus.consume(timeout=self.poll_timeout_sec)
            if event is None:
                continue
            self.event_bus.task_done()  # mark consumed immediately — defensive against early exits

            start = time.perf_counter()
            loop_id = f"loop_{uuid4().hex[:8]}"

            try:
                # ── policy checkpoint A: event entry guard ──
                if self.policy_engine:
                    policy_decision = self.policy_engine.evaluate(
                        event.type,
                        context={"source": event.source, "task_id": loop_id},
                    )
                    if policy_decision.verdict == Verdict.QUARANTINE:
                        logger.critical("policy: quarantine triggered — blocking event")
                        self.system_state["policy_state"] = self.policy_engine.get_state()
                        if self._ws_manager:
                            self._ws_manager.broadcast_sync("policy_state", self.system_state["policy_state"])
                        continue
                    if policy_decision.verdict == Verdict.DENY:
                        logger.warning("policy: event denied — %s", policy_decision.reason)
                        self.system_state["policy_state"] = self.policy_engine.get_state()
                        if self._ws_manager:
                            self._ws_manager.broadcast_sync("policy_state", self.system_state["policy_state"])
                        continue

                self.memory_api.append_event(event)
                plan = self.planner.plan(event)

                result_summary = "no action"
                selected_agent = plan.agent
                reply = ""

                now_ts = time.time()

                if event.type == "user_message":
                    user_text = str(event.payload.get("text", ""))
                    self.world_model.apply_user_message(user_text)
                    self.proactive_state["last_user_message_ts"] = now_ts

                    self._memory_ingestor.ingest_user_message(
                        text=user_text,
                        event_id=event.id,
                        source=event.source,
                        active_tasks=self.world_model.active_tasks,
                    )

                    if self.context_builder:
                        user_id = str(event.payload.get("user_id", "default"))
                        ctx = self.context_builder.build(
                            user_id=user_id,
                            text=user_text,
                        )
                        # inject context into task for agent consumption
                        plan.task.payload["context"] = ctx
                        self.system_state["last_context_summary"] = {
                            "persona_loaded": bool(ctx.get("persona")),
                            "memory_items": len(ctx.get("memories") or []),
                            "active_tasks": len(ctx.get("active_tasks", [])),
                            "entities": len(ctx.get("recent_entities", [])),
                            "summary": ctx.get("context_summary", ""),
                        }

                if event.type == "maintenance":
                    decision = self.proactive_engine.evaluate_stagnation(
                        world_model=self.world_model.to_dict(),
                        proactive_state=self.proactive_state,
                        now_ts=now_ts,
                    )
                    self.system_state["last_proactive_reason"] = decision.reason
                    logger.info(
                        "proactive decision: %s",
                        decision.reason,
                    )

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

                    if not blocked:
                        selected_agent = self.agent_router.route(plan.agent, plan.task)
                        logger.debug("routing: plan.agent=%s selected_agent=%s task.kind=%s",
                                     plan.agent, selected_agent, plan.task.kind)

                        # ── streaming mode: chat_agent only ──
                        # Agents mapped to executors (search/code/docs → file)
                        # require token + boundary checks and cannot stream.
                        # Fall back to the gated path for those agents.
                        stream_requested = event.payload.get("stream") and event.correlation_id
                        agent_needs_executor = AGENT_EXECUTOR_MAP.get(selected_agent) and self._executors

                        if stream_requested and not agent_needs_executor:
                            result, agent_duration_ms = self._execute_agent_stream(
                                selected_agent, plan.task, event.correlation_id,
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
                                    tm = self.policy_engine.token_manager
                                    token = tm.issue(
                                        loop_id, exe_type or "agent",
                                        ttl_seconds=300, budget_tokens=10,
                                    )
                                    agent_tok = token.token_id

                            result, agent_duration_ms = self._execute_agent(
                                selected_agent, plan.task, agent_exe,
                                self.policy_engine.token_manager if self.policy_engine else None,
                                agent_tok,
                            )
                            reply = result.content

                        # ── WS broadcast: push result to connected clients ──
                        if self._ws_manager and event.correlation_id:
                            self._ws_manager.broadcast_sync("chat_reply", {
                                "task_id": event.correlation_id,
                                "ok": result.ok,
                                "reply": reply,
                                "selected_agent": selected_agent,
                                "loop_id": loop_id,
                                "duration_ms": agent_duration_ms,
                                "event_id": event.id,
                            })

                    # ── self-model feedback: prediction error + state recording ──
                    if not blocked:
                        prev_focus = self.world_model.focus
                        prev_reply = self.world_model.last_reply

                        self.world_model.apply_agent_result(
                            reply=reply,
                            selected_agent=selected_agent,
                            loop_id=loop_id,
                        )

                        # compute prediction error if tracker is wired
                        prediction_error = 0.0
                        if self.prediction_tracker and prev_focus:
                            expected = f"agent {selected_agent} responds about {prev_focus[:60]}"
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

                    if event.type == "reminder_trigger":
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
                            "agent_duration_ms": agent_duration_ms,
                        },
                        importance=0.7 if event.type == "reminder_trigger" else 0.6,
                    )

                    # ── tiered memory (S1-S3) ─────────────────
                    if self.tiered_memory and not blocked:
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
                        )

                    # ── world model: entity extraction → graph → S4 ──
                    if not blocked and reply and self.world_model:
                        try:
                            future = self._agent_pool.submit(
                                entity_extractor.extract_from_reply, reply
                            )
                            entities, relations = future.result(timeout=5.0)
                        except FutureTimeoutError:
                            entities, relations = [], []
                        for e in entities:
                            self.world_model.upsert_entity(
                                e["type"], e["name"], e.get("properties", {}),
                            )
                            # broadcast entity creation
                            if self._ws_manager:
                                self._ws_manager.broadcast_sync("entity_created", {
                                "type": e["type"], "name": e["name"],
                                "properties": e.get("properties", {}),
                            })
                        for r in relations:
                            self.world_model.link(
                                r["source"], r["target"], r["relation"],
                                weight=r.get("weight", 1.0),
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

                if event.correlation_id:
                    self.result_registry.fulfill(
                        event.correlation_id,
                        {
                            "ok": True,
                            "reply": reply,
                            "selected_agent": selected_agent,
                            "loop_id": loop_id,
                            "event_id": event.id,
                            "event_type": event.type,
                            "duration_ms": duration_ms,
                        },
                    )

                logger.info(
                    "loop event=%s decision=%s agent=%s duration_ms=%s",
                    event.type,
                    plan.decision,
                    selected_agent,
                    duration_ms,
                )
            except sqlite3.OperationalError as e:
                is_transient = any(tag in str(e).lower() for tag in self._TRANSIENT_SQLITE)
                if is_transient:
                    logger.warning("cognition loop transient sqlite error: %s", e)
                    self._consecutive_errors += 1
                    if self._consecutive_errors >= 10:
                        logger.critical("policy: quarantine after %d consecutive transient errors", self._consecutive_errors)
                        if self.policy_engine:
                            self.policy_engine.transition("violation_detected")
                            self.system_state["policy_state"] = self.policy_engine.get_state()
                        self._consecutive_errors = 0
                else:
                    logger.exception("cognition loop sqlite error")
                    if self.policy_engine:
                        self.policy_engine.transition("violation_detected")
                        self.system_state["policy_state"] = self.policy_engine.get_state()
                        logger.critical("policy: entering quarantine due to sqlite error")
            except Exception:
                logger.exception("cognition loop error")
                self._consecutive_errors += 1
                if self._consecutive_errors >= 3:
                    if self.policy_engine:
                        self.policy_engine.transition("violation_detected")
                        self.system_state["policy_state"] = self.policy_engine.get_state()
                    logger.critical("policy: quarantine after %d consecutive errors", self._consecutive_errors)
                    self._consecutive_errors = 0
            else:
                self._consecutive_errors = 0
            finally:
                self.result_registry.cleanup(ttl_sec=self.result_ttl_sec)
                self.system_state["pending_events"] = self.event_bus.size()
                self.system_state["pending_results"] = self.result_registry.size()
