import threading
import time
from uuid import uuid4
import logging

from agent_os.orchestrator import AgentOrchestrator
from agent_os.router import AgentRouter
from core.context_builder import ContextBuilder
from core.planner import Planner
from core.prediction import PredictionTracker
from core.proactive_engine import ProactiveEngine
from event.event_bus import EventBus
from event.event_schema import Event, TraceRecord
from memory.memory_api import MemoryAPI
from memory.importance_scorer import ImportanceFeatures
from memory.memory_governor import MemoryGovernor
from memory.memory_schema import MemoryRecord, MemoryType
from persona.self_model_store import SelfModelStore
from runtime.result_registry import ResultRegistry
from world.world_model import WorldModel


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
        proactive_state: dict,
        world_model: WorldModel,
        system_state: dict,
        poll_timeout_sec: float = 0.5,
        result_ttl_sec: float = 60.0,
        context_builder: ContextBuilder | None = None,
        enable_v02_pipeline: bool = False,
        prediction_tracker: PredictionTracker | None = None,
        self_model_store: SelfModelStore | None = None,
        self_model: dict | None = None,
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
        self.enable_v02_pipeline = enable_v02_pipeline
        self.prediction_tracker = prediction_tracker
        self.self_model_store = self_model_store
        self.self_model = self_model

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

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

    def _run_forever(self) -> None:
        logger = logging.getLogger("eva.cognition_loop")
        while not self._stop_event.is_set():
            event = self.event_bus.consume(timeout=self.poll_timeout_sec)
            if event is None:
                continue

            start = time.perf_counter()
            loop_id = f"loop_{uuid4().hex[:8]}"

            try:
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

                    if self.memory_governor:
                        record = MemoryRecord(
                            id=str(uuid4()),
                            memory_type=MemoryType.EPISODIC,
                            content=user_text,
                            source_event_id=event.id,
                            confidence=0.7,
                            ttl_seconds=None,
                            conflict_keys=[],
                        )
                        features = ImportanceFeatures(
                            user_explicit=user_text.startswith("/"),
                            goal_related=self._text_matches_active_tasks(user_text),
                            blocker_related=False,
                            persona_related=self._text_matches_persona(user_text),
                            repeated_mentions=0,
                            source_reliability=0.9 if event.source == "user" else 0.6,
                            emotional_intensity=self._estimate_emotional_intensity(user_text),
                            age_hours=0.0,
                            self_model_delta=0.0,
                            prediction_error=0.0,
                        )
                        self.memory_governor.ingest(record, features)

                    if self.enable_v02_pipeline and self.context_builder:
                        user_id = str(event.payload.get("user_id", "default"))
                        ctx = self.context_builder.build(
                            user_id=user_id,
                            text=user_text,
                        )
                        world_ctx = ctx.get("world_context") or {}
                        self.system_state["last_context_summary"] = {
                            "persona_loaded": bool(ctx.get("persona")),
                            "memory_items": len(ctx.get("memories") or []),
                            "world_nodes": len(world_ctx.get("nodes") or []),
                            "world_edges": len(world_ctx.get("edges") or []),
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
                    selected_agent = self.agent_router.route(plan.agent, plan.task)
                    result, agent_duration_ms = self.orchestrator.execute(selected_agent, plan.task)
                    reply = result.content

                    # ── self-model feedback: prediction error + state recording ──
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
            except Exception:
                logger.exception("cognition loop error")
            finally:
                self.result_registry.cleanup(ttl_sec=self.result_ttl_sec)
                self.system_state["pending_events"] = self.event_bus.size()
                self.system_state["pending_results"] = self.result_registry.size()
                self.event_bus.task_done()

    # ── importance feature helpers ────────────────────────────

    def _text_matches_active_tasks(self, text: str) -> bool:
        tasks = self.world_model.active_tasks
        if not tasks:
            return False
        text_lower = text.lower()
        for task in tasks:
            desc = str(task.get("description", "")).lower()
            name = str(task.get("name", "")).lower()
            if desc and desc in text_lower:
                return True
            if name and name in text_lower:
                return True
        return False

    def _text_matches_persona(self, text: str) -> bool:
        if not self.self_model:
            return False
        text_lower = text.lower()
        capabilities = self.self_model.get("capabilities", [])
        for cap in capabilities:
            if str(cap).lower() in text_lower:
                return True
        identity = str(self.self_model.get("identity", "")).lower()
        if identity and identity in text_lower:
            return True
        return False

    @staticmethod
    def _estimate_emotional_intensity(text: str) -> float:
        markers_high = ["urgent", "紧急", "asap", "!!!", "critical", "严重"]
        markers_med = ["worried", "担心", "frustrated", "important", "重要"]
        text_lower = text.lower()
        if any(m in text_lower for m in markers_high):
            return 0.8
        if any(m in text_lower for m in markers_med):
            return 0.4
        return 0.1
