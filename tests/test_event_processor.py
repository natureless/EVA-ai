"""Execution receipts and thread lifecycle remain independent contracts."""

from __future__ import annotations

import sqlite3
import threading
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent_os.orchestrator import AgentOrchestrator
from agent_os.registry import AgentRegistry
from agent_os.router import AgentRouter
from agents.base_agent import AgentResult, AgentTask, BaseAgent
from core.cognition_loop import CognitionLoop
from core.context_builder import ContextBuilder
from core.event_processor import EventProcessor
from core.planner import Planner
from core.policy_engine import PolicyEngine
from core.proactive_engine import ProactiveEngine
from event.event_bus import EventBus
from event.event_schema import Event
from memory.memory_api import MemoryAPI
from runtime.agent_worker import ThreadAgentWorkerBackend
from runtime.result_registry import ResultRegistry
from runtime.controller import RuntimeController
from world.world_model import WorldModelGraph
from packages.kernel.processing_episodes import ProcessingEpisodes


class RecordingAgent(BaseAgent):
    name = "chat_agent"

    def __init__(self) -> None:
        self.contexts: list[dict] = []

    def can_handle(self, task: AgentTask) -> bool:
        return True

    def run(self, task: AgentTask) -> AgentResult:
        self.contexts.append(deepcopy(task.payload.get("context", {})))
        return AgentResult(
            ok=True,
            agent=self.name,
            content="```eva_response\n"
            '{"message":"A working inference", "claims":['
            '{"text":"A working inference", "status":"assistant_inference"}]}'
            "\n```",
            summary="deterministic test response",
        )


@pytest.fixture
def processor_parts(monkeypatch):
    registry = AgentRegistry()
    agent = RecordingAgent()
    registry.register(agent)
    orchestrator = AgentOrchestrator(registry)
    dependencies = {
        "event_bus": EventBus(),
        "memory_api": MagicMock(spec=MemoryAPI),
        "memory_governor": None,
        "planner": Planner(),
        "agent_router": AgentRouter(registry),
        "orchestrator": orchestrator,
        "result_registry": ResultRegistry(),
        "proactive_engine": ProactiveEngine(),
        "proactive_state": {},
        "world_model": WorldModelGraph(),
        "system_state": {},
    }
    # The execution contract is exercised with a deterministic local agent.
    monkeypatch.setattr(
        "core.event_processor.entity_extractor.extract_from_reply", lambda _: ([], [])
    )
    return dependencies, agent


def test_processing_episode_is_written_once_before_result_notification(
    processor_parts, tmp_path
):
    dependencies, agent = processor_parts
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    dependencies["result_registry"] = ResultRegistry(
        receipt_observer=journal.record_receipt
    )
    processor = EventProcessor(**dependencies, episode_recorder=journal)
    event = Event(
        type="user_message",
        source="test",
        payload={"text": "private"},
        correlation_id="episode-task",
    )
    dependencies["result_registry"].create(event.correlation_id, event_id=event.id)
    try:
        receipt = processor.process_event(event)
        saved = journal.get_by_event(event.id)
        assert receipt["ok"] and saved.result.status == "succeeded"
        assert saved.actions[0].status == "completed" and len(agent.contexts) == 1
        assert saved.metadata["state_reference_kind"] == "world_snapshot_unversioned"
        assert saved.state_before.version is None and saved.state_after.version is None
        assert processor.process_event(event) == receipt and len(agent.contexts) == 1
    finally:
        processor.shutdown_owned_backend()
        journal.close()


def test_audit_begin_failure_prevents_agent_or_memory_effects(
    processor_parts, tmp_path, monkeypatch
):
    dependencies, agent = processor_parts
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    processor = EventProcessor(**dependencies, episode_recorder=journal)
    monkeypatch.setattr(
        journal,
        "begin",
        lambda *args: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")
        ),
    )
    event = Event(
        type="user_message",
        source="test",
        payload={"text": "hello"},
        correlation_id="failed-audit",
    )
    dependencies["result_registry"].create(event.correlation_id, event_id=event.id)
    try:
        assert processor.process_event(event)["error"] == "storage_error"
        assert agent.contexts == []
        dependencies["memory_api"].append_event.assert_not_called()
        assert journal.stats()["processing"] == 0
    finally:
        processor.shutdown_owned_backend()
        journal.close()


def test_recovered_episode_blocks_same_event_execution_in_fresh_registry(
    processor_parts, tmp_path
):
    dependencies, agent = processor_parts
    path = tmp_path / "eva.db"
    event = Event(
        type="user_message",
        source="test",
        payload={"text": "hello"},
        correlation_id="restarted-task",
    )
    first = ProcessingEpisodes(path)
    first.begin(event, "old-loop", {})
    first.action_started(event.id, "old-loop", "chat_agent")
    first.close()
    journal = ProcessingEpisodes(path)
    assert journal.recover() == 1
    processor = EventProcessor(**dependencies, episode_recorder=journal)
    dependencies["result_registry"].create(event.correlation_id, event_id=event.id)
    try:
        receipt = processor.process_event(event)
        assert receipt["error"] == "episode_already_recorded" and agent.contexts == []
        assert journal.get_by_event(event.id).result.status == "unknown"
    finally:
        processor.shutdown_owned_backend()
        journal.close()


def test_episode_failure_after_terminal_preserves_reply_and_draft(
    processor_parts, tmp_path
):
    dependencies, agent = processor_parts
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    dependencies["result_registry"] = ResultRegistry(
        receipt_observer=journal.record_receipt
    )
    journal._conn.set_authorizer(
        lambda op, table, *args: sqlite3.SQLITE_DENY
        if op == sqlite3.SQLITE_INSERT and table == "episodes"
        else sqlite3.SQLITE_OK
    )
    processor = EventProcessor(**dependencies, episode_recorder=journal)
    event = Event(
        type="user_message",
        source="test",
        payload={"text": "hello"},
        correlation_id="retained-reply",
    )
    dependencies["result_registry"].create(event.correlation_id, event_id=event.id)
    try:
        receipt = processor.process_event(event)
        assert receipt["ok"] and len(agent.contexts) == 1
        assert dependencies["result_registry"].peek(event.correlation_id) == receipt
        assert journal.stats()["processing"] == 1
        assert dependencies["result_registry"].stats()["receipt_observer_errors"] == 1
        journal._conn.set_authorizer(None)
        journal.record_receipt(receipt)
        assert journal.get_by_event(event.id).result.status == "succeeded"
    finally:
        processor.shutdown_owned_backend()
        journal.close()


def test_running_deadline_seals_unknown_and_late_completion_does_not_upgrade_episode(
    processor_parts, tmp_path, monkeypatch
):
    dependencies, agent = processor_parts
    clock = [0.0]
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    registry = ResultRegistry(
        clock=lambda: clock[0],
        pending_timeout_sec=10,
        receipt_observer=journal.record_receipt,
    )
    dependencies["result_registry"] = registry
    processor = EventProcessor(**dependencies, episode_recorder=journal)
    event = Event(
        type="user_message",
        source="test",
        payload={"text": "hello"},
        correlation_id="timeout-audit",
    )
    registry.create(event.correlation_id, event_id=event.id)
    entered, release = threading.Event(), threading.Event()
    run = agent.run

    def blocked(task):
        entered.set()
        assert release.wait(5)
        return run(task)

    monkeypatch.setattr(agent, "run", blocked)
    thread = threading.Thread(target=processor.process_event, args=(event,))
    try:
        thread.start()
        assert entered.wait(2)
        clock[0] = 11
        assert (
            registry.peek(event.correlation_id)["terminal_state"] == "outcome_unknown"
        )
        before = journal.get_by_event(event.id).as_record()
        assert before["result"]["status"] == "unknown"
        assert before["actions"][0]["status"] == "unknown"
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert journal.get_by_event(event.id).as_record() == before
    finally:
        release.set()
        thread.join(5)
        processor.shutdown_owned_backend()
        journal.close()


@pytest.mark.parametrize("failing", [False, True])
def test_events_without_request_ids_still_seal_audit_records(
    processor_parts, tmp_path, failing
):
    dependencies, agent = processor_parts
    journal = ProcessingEpisodes(tmp_path / "eva.db")
    processor = EventProcessor(**dependencies, episode_recorder=journal)
    if failing:
        dependencies["memory_api"].append_event.side_effect = RuntimeError(
            "injected storage failure"
        )
    event = Event(type="user_message", source="test", payload={"text": "hello"})
    try:
        processor.process_event(event)
        saved = journal.get_by_event(event.id)
        assert saved.result.status == ("failed" if failing else "succeeded")
        assert journal.stats()["processing"] == 0
    finally:
        processor.shutdown_owned_backend()
        journal.close()


def test_service_executes_reviewed_receipt_without_consuming_or_starting_loop(
    processor_parts,
):
    dependencies, agent = processor_parts
    tiered = MagicMock()
    processor = EventProcessor(**dependencies, tiered_memory=tiered)
    event = Event(
        type="user_message",
        source="user",
        payload={"text": "hello"},
        correlation_id="direct-service",
    )
    dependencies["result_registry"].create(event.correlation_id)
    dependencies["event_bus"].publish(event)
    context = {"state_version": 7, "world": {"source": "observation"}}
    try:
        receipt = processor.process_event(event, cognitive_context=context)

        assert receipt["ok"] is True
        assert receipt["reply"] == "A working inference"
        assert receipt["review"]["schema_version"] == 1
        assert receipt["review"]["fact_verified"] is False
        assert dependencies["result_registry"].peek(event.correlation_id) == receipt
        assert dependencies["event_bus"].size() == 1
        assert agent.contexts[0]["minimal_brain"] == context
        assert (
            tiered.ingest.call_args.kwargs["origin"]["epistemic_status"]
            == "assistant_inference"
        )
        dependencies["memory_api"].write_trace.assert_called_once()
        assert processor.stats["total_processed"] == 1
        assert processor.is_idle is True
        assert not hasattr(processor, "_threads")
    finally:
        processor.request_stop()
        assert processor.shutdown_owned_backend()


def test_service_keeps_policy_entry_guard_before_memory_or_agent_execution(
    processor_parts,
):
    dependencies, agent = processor_parts
    policy = PolicyEngine()
    policy.transition("violation_detected")
    processor = EventProcessor(**dependencies, policy_engine=policy)
    event = Event(type="user_message", source="user", payload={"text": "hello"})
    try:
        receipt = processor.process_event(event)

        assert receipt["ok"] is False
        assert receipt["error"] == "policy_denied"
        assert agent.contexts == []
        dependencies["memory_api"].append_event.assert_not_called()
        assert processor.is_idle is True
    finally:
        processor.request_stop()
        assert processor.shutdown_owned_backend()


def test_reply_extraction_cannot_promote_model_claimed_fact(
    processor_parts, monkeypatch, tmp_path
):
    from memory.provenance import EpistemicStatus, provenance
    from memory.sqlite_store import SQLiteStore
    from memory.tiered_store import TieredMemoryManager

    dependencies, _ = processor_parts
    forged = provenance(EpistemicStatus.VERIFIED_FACT, source="claimed_validator")
    monkeypatch.setattr(
        "core.event_processor.entity_extractor.extract_from_reply",
        lambda _: (
            [
                {
                    "type": "project",
                    "name": "Claim",
                    "properties": {"state": "certain"},
                    "provenance": forged,
                }
            ],
            [
                {
                    "source": "user",
                    "target": "project_claim",
                    "relation": "owns",
                    "provenance": forged,
                }
            ],
        ),
    )
    store = SQLiteStore(tmp_path / "extracted.db")
    store.init_db()
    tiered = TieredMemoryManager(store)
    processor = EventProcessor(**dependencies, tiered_memory=tiered)
    event = Event(type="user_message", source="user", payload={"text": "hello"})
    try:
        result = processor.process_event(event)
        assert result["ok"] is True
        expected = provenance(
            EpistemicStatus.ASSISTANT_INFERENCE,
            source="chat_agent",
            source_event_id=event.id,
        )
        restored = WorldModelGraph.from_dict(dependencies["world_model"].to_dict())
        restored.load_from_store(tiered.s4)
        assert restored.get_entity("project_claim").provenance == expected
        assert restored.get_edges()[0].provenance == expected
        assert tiered.s4.get_entity("project_claim")["provenance"] == expected
        context = ContextBuilder(world_model=restored).build(
            user_id="default", text="Claim"
        )
        assert context["recent_entity_records"][0]["provenance"] == expected
    finally:
        processor.request_stop()
        assert processor.shutdown_owned_backend()
        store.close()


def test_expired_queued_request_never_executes_or_writes_memory(processor_parts):
    dependencies, agent = processor_parts
    clock = SimpleNamespace(now=0.0)
    registry = ResultRegistry(clock=lambda: clock.now, pending_timeout_sec=1)
    dependencies["result_registry"] = registry
    event = Event(
        type="user_message",
        source="user",
        correlation_id="expired",
        payload={"text": "hello"},
    )
    registry.create("expired", event_id=event.id)
    clock.now = 2
    processor = EventProcessor(**dependencies)
    try:
        receipt = processor.process_event(event)
        assert receipt["terminal_state"] == "expired"
        assert receipt["execution_state"] == "not_started"
        assert agent.contexts == []
        dependencies["memory_api"].append_event.assert_not_called()
    finally:
        processor.request_stop()
        assert processor.shutdown_owned_backend()


@pytest.mark.parametrize(
    "reason", ["cancelled", "timeout", "worker_crash", "protocol_error"]
)
def test_worker_uncertainty_does_not_claim_execution_has_stopped(
    processor_parts, reason
):
    dependencies, _ = processor_parts
    processor = EventProcessor(**dependencies)
    try:
        receipt = processor._deliver_result(
            Event(type="user_message", source="user"),
            "loop",
            AgentResult(
                False, "chat_agent", "cancelled during shutdown", "", {"status": reason}
            ),
            1,
        )
        assert (
            receipt["terminal_state"] == receipt["execution_state"] == "outcome_unknown"
        )
        assert "执行结果尚不确定" in receipt["reply"]
        assert "cancelled" not in receipt["reply"]
    finally:
        processor.request_stop()
        assert processor.shutdown_owned_backend()


def test_running_expiry_and_failed_broadcast_cannot_replace_canonical_receipt(
    processor_parts,
):
    dependencies, _ = processor_parts
    clock = SimpleNamespace(now=0.0)
    registry = ResultRegistry(clock=lambda: clock.now, pending_timeout_sec=1)
    dependencies["result_registry"] = registry
    ws = MagicMock()
    ws.broadcast_sync.side_effect = RuntimeError("delivery unavailable")
    processor = EventProcessor(**dependencies, ws_manager=ws)
    event = Event(type="user_message", source="user", correlation_id="late")
    registry.create("late")
    registry.begin("late")
    clock.now = 2
    try:
        late = processor._deliver_result(
            event, "loop", AgentResult(True, "chat_agent", "late success", ""), 1
        )
        assert late == registry.peek("late")
        assert late["terminal_state"] == "outcome_unknown"
        ws.broadcast_sync.assert_not_called()
        registry.create("done")
        event.correlation_id = "done"
        result = processor._deliver_result(
            event, "loop", AgentResult(True, "chat_agent", "stored", ""), 1
        )
        assert result["ok"] is True and registry.peek("done") == result
        ws.broadcast_sync.assert_called_once()
    finally:
        processor.request_stop()
        assert processor.shutdown_owned_backend()


@pytest.mark.parametrize("mode", ["legacy", "minimal"])
@pytest.mark.parametrize("stop_while_busy", [False, True])
def test_loaded_runtime_keeps_terminal_for_every_admitted_request(
    processor_parts, mode, stop_while_busy
):
    dependencies, agent = processor_parts
    bus = EventBus(max_queue_size=24)
    dependencies["event_bus"] = bus
    registry = dependencies["result_registry"]
    entered, release = threading.Event(), threading.Event()

    def blocked_agent(task):
        entered.set()
        assert release.wait(5)
        return AgentResult(True, "chat_agent", "completed", "completed")

    agent.run = blocked_agent
    processor = EventProcessor(**dependencies)
    if mode == "legacy":
        consumer = CognitionLoop(bus, processor=processor, poll_timeout_sec=0.005)
    else:
        from packages.minimal_brain.kernel import KernelConfig, MinimalBrainKernel

        consumer = MinimalBrainKernel(
            event_bus=bus,
            process_event=lambda event, context: processor.process_event(
                event, cognitive_context=context
            ),
            on_reject=processor.reject_event,
            processor_ready=lambda: processor._agent_worker.is_idle,
            config=KernelConfig(queue_capacity=4, poll_interval=0.005),
        )
    controller = RuntimeController(
        mode=mode,
        consumer=consumer,
        processor=processor,
        event_bus=bus,
        worker_backend=processor._agent_worker,
        system_state=dependencies["system_state"],
    )

    def finish_shutdown():
        deadline = time.monotonic() + 3
        while not controller.stop(timeout=0.1):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True

    def publish(index):
        key = f"load-{index}"
        event = Event(
            type="user_message",
            source="user",
            correlation_id=key,
            payload={"text": "hello"},
        )
        assert registry.create(key, event_id=event.id)
        admitted = bus.publish(event)
        if not admitted:
            registry.pop(key)
        return key, admitted

    controller.start()
    try:
        assert publish("first")[1]
        assert entered.wait(1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(publish, range(500)))
        accepted = [key for key, admitted in outcomes if admitted] + ["load-first"]
        assert len(accepted) > 1
        assert bus.size() <= 24
        if stop_while_busy:
            controller.request_stop()
            for key in accepted:
                receipt = registry.wait(key, timeout=1)
                assert receipt is not None, key
            assert (
                controller.stop(timeout=0) is False
            )  # Underlying agent still blocked.
        release.set()
        for key in accepted:
            receipt = registry.wait(key, timeout=3)
            assert receipt is not None, key
            assert receipt["terminal_state"] in {
                "succeeded",
                "rejected",
                "outcome_unknown",
            }
            assert registry.peek(key) == receipt
        assert finish_shutdown()
        assert bus._queue.unfinished_tasks == 0
        assert bus.stats()["evicted_background"] == 0
    finally:
        release.set()
        assert finish_shutdown()
        assert processor.shutdown_owned_backend()


def test_storage_failure_releases_service_activity_and_next_event_can_run(
    processor_parts,
):
    dependencies, _ = processor_parts
    dependencies["memory_api"].append_event.side_effect = [
        sqlite3.OperationalError("database is locked"),
        None,
    ]
    processor = EventProcessor(**dependencies)
    try:
        failure = processor.process_event(Event(type="system_tick", source="test"))
        assert failure["error"] == "storage_error"
        assert processor.stats["active_events"] == 0
        assert processor.stats["consecutive_errors"] == 1

        recovery = processor.process_event(Event(type="system_tick", source="test"))
        assert recovery["ok"] is True
        assert processor.stats["consecutive_errors"] == 0
        assert processor.stats["total_processed"] == 1
        assert processor.is_idle is True
    finally:
        processor.request_stop()
        assert processor.shutdown_owned_backend()


def test_service_stop_rejects_new_work_through_existing_result_registry(
    processor_parts,
):
    dependencies, agent = processor_parts
    processor = EventProcessor(**dependencies)
    event = Event(
        type="user_message",
        source="user",
        correlation_id="stopped",
        payload={"text": "hello"},
    )
    dependencies["result_registry"].create(event.correlation_id)
    processor.request_stop()
    try:
        receipt = processor.process_event(event)

        assert receipt["error"] == "runtime_stopping"
        assert dependencies["result_registry"].peek(event.correlation_id) == receipt
        dependencies["memory_api"].append_event.assert_not_called()
        assert agent.contexts == []
        assert processor.stats["active_events"] == 0
    finally:
        assert processor.shutdown_owned_backend()


def test_legacy_construction_and_processing_delegate_to_one_service(processor_parts):
    dependencies, _ = processor_parts
    loop = CognitionLoop(**dependencies, poll_timeout_sec=0.01)
    try:
        receipt = loop.process_event(Event(type="system_tick", source="test"))
        assert isinstance(loop.processor, EventProcessor)
        assert receipt["ok"] is True
        assert loop._threads == []
        assert (
            loop.stats["total_processed"]
            == loop.processor.stats["total_processed"]
            == 1
        )
        assert loop.memory_api is dependencies["memory_api"]
    finally:
        assert loop.stop(timeout=1.0)


def test_stop_retains_blocked_consumer_and_retry_finishes_shutdown(processor_parts):
    dependencies, _ = processor_parts
    entered, release = threading.Event(), threading.Event()

    def block_storage(_event):
        entered.set()
        assert release.wait(5.0)

    dependencies["memory_api"].append_event.side_effect = block_storage
    processor = EventProcessor(**dependencies)
    loop = CognitionLoop(
        event_bus=dependencies["event_bus"], processor=processor, poll_timeout_sec=0.01
    )
    loop.start()
    assert loop.is_running
    dependencies["event_bus"].publish(Event(type="system_tick", source="test"))
    try:
        assert entered.wait(1.0)
        assert loop.stop(timeout=0.01) is False
        assert len(loop._threads) == 1
        assert loop._threads[0].is_alive()
        assert loop.is_running is False
        assert processor.is_idle is False
        with pytest.raises(RuntimeError, match="have not stopped"):
            loop.start()
    finally:
        release.set()
        assert loop.stop(timeout=1.0)
    assert loop._threads == []
    assert processor.stats["active_events"] == 0
    assert dependencies["event_bus"]._queue.unfinished_tasks == 0


def test_idle_consumer_does_not_hide_real_backend_work_or_close_it_early(
    processor_parts,
):
    dependencies, _ = processor_parts
    entered, release = threading.Event(), threading.Event()
    processor = EventProcessor(**dependencies)
    loop = CognitionLoop(event_bus=dependencies["event_bus"], processor=processor)

    def real_work():
        entered.set()
        assert release.wait(5.0)

    future = processor._agent_worker.submit(real_work)
    try:
        assert entered.wait(1.0)
        assert processor.stats["active_events"] == 0
        assert processor.is_idle is False
        assert loop.stop(timeout=0.01) is False
        assert processor._closed is False
        with pytest.raises(RuntimeError, match="execution is active"):
            processor.reset_stop()
    finally:
        release.set()
        future.result(timeout=1.0)
        assert loop.stop(timeout=1.0)
    assert processor._closed is True


def test_injected_worker_ownership_stays_with_composition_root(processor_parts):
    dependencies, _ = processor_parts
    worker = ThreadAgentWorkerBackend(dependencies["orchestrator"])
    worker.shutdown = MagicMock(wraps=worker.shutdown)
    processor = EventProcessor(**dependencies, agent_worker_backend=worker)
    loop = CognitionLoop(
        event_bus=dependencies["event_bus"], processor=processor, poll_timeout_sec=0.01
    )
    try:
        loop.start()
        assert loop.stop(timeout=1.0)
        worker.shutdown.assert_not_called()
        loop.start()
        assert loop.is_running
        assert loop.stop(timeout=1.0)
        worker.shutdown.assert_not_called()
    finally:
        worker.shutdown()


def test_scheduler_cannot_publish_and_consume_on_different_buses(processor_parts):
    dependencies, _ = processor_parts
    processor = EventProcessor(**dependencies)
    try:
        with pytest.raises(ValueError, match="share one EventBus"):
            CognitionLoop(event_bus=EventBus(), processor=processor)
    finally:
        processor.request_stop()
        assert processor.shutdown_owned_backend()
