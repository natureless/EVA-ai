"""Run a no-network, temporary-storage acceptance demonstration.

    python -m packages.minimal_brain.demo
    python -m packages.minimal_brain.demo --output artifacts/minimal-brain-demo.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import threading
import time

from core.llm_adapter import MockLLM
from event.event_bus import EventBus
from event.event_schema import Event
from packages.minimal_brain.kernel import KernelConfig, MinimalBrainKernel
from runtime.file_utils import atomic_json_save
from world.snapshot_store import SnapshotStore


def _until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("minimal brain demonstration did not finish")
        time.sleep(0.01)


def run_demo() -> dict:
    started, release = threading.Event(), threading.Event()
    executed = []
    llm = MockLLM()  # Explicitly bypass provider configuration and credentials.
    pressure = {"pressure": 0.0}
    bus = EventBus(max_queue_size=32, overflow_policy="drop_newest")
    first = Event(type="user_message", source="demo", payload={"text": "Explain the current cognitive task"})
    maintenance = Event(type="maintenance", source="demo", payload={})
    followup = Event(type="user_message", source="demo", payload={"text": "Use the earlier feedback"})

    def process(event, context):
        executed.append(event.id)
        if event.id == first.id:
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("demo cognition release was not signalled")
        reply = llm.chat([
            {"role": "system", "content": f"Current context: {context['goal']['summary']}"},
            {"role": "user", "content": str(event.payload.get("text", event.type))},
        ])
        return {"ok": True, "reply": reply, "selected_agent": "mock_cognition"}

    kernel = MinimalBrainKernel(
        bus, process, resource_probe=lambda: dict(pressure),
        config=KernelConfig(queue_capacity=16, poll_interval=0.01),
    )
    try:
        kernel.start()
        assert bus.publish(first)
        if not started.wait(timeout=3):
            raise TimeoutError("slow cognition did not start")
        baseline_ticks = kernel.stats["node_ticks"]["body"]
        pressure["pressure"] = 0.95
        assert bus.publish(maintenance)
        assert bus.publish(followup)
        _until(lambda: (
            kernel.stats["node_ticks"]["body"] >= baseline_ticks + 4
            and bool(kernel.snapshot()["workspace"])
            and kernel.snapshot()["workspace"][0]["event_id"] == maintenance.id
        ))
        while_blocked = kernel.snapshot()
        assert len(executed) == 1, "no second action may use the occupied processor slot"
        release.set()
        _until(lambda: kernel.stats["completed"] == 3)
        assert bus.publish(first)
        _until(lambda: kernel.stats["duplicates"] == 1)
        pressure["pressure"] = 0.0
        _until(lambda: kernel.snapshot()["body"]["pressure"] == 0.0)
        assert kernel.stop()
        saved = kernel.snapshot()
        with tempfile.TemporaryDirectory(prefix="eva-minimal-brain-") as tmp:
            store = SnapshotStore(Path(tmp), Path(tmp) / "latest.json")
            store.save_latest({"minimal_brain": saved})
            loaded = store.load_latest()["minimal_brain"]

            def must_not_execute(event, context):
                raise AssertionError("recovery must not replay external actions")

            restored = MinimalBrainKernel(
                EventBus(), must_not_execute, initial_snapshot=loaded,
            )
            try:
                restored.step()
                restored_state = restored.snapshot()
            finally:
                restored.stop()
        return {
            "status": "passed",
            "provider": llm.provider,
            "network_calls": 0,
            "slow_cognition_blocked": {
                "body_ticks_advanced": while_blocked["stats"]["node_ticks"]["body"] - baseline_ticks,
                "attention_ticks": while_blocked["stats"]["node_ticks"]["attention"],
                "processor_busy": while_blocked["stats"]["processor_busy"],
                "workspace_first": while_blocked["workspace"][0]["event_type"],
            },
            "execution_order": [
                {first.id: "first_user_request", maintenance.id: "maintenance", followup.id: "second_user_request"}[key]
                for key in executed
            ],
            "completed_events": saved["stats"]["completed"],
            "duplicate_deliveries_ignored": saved["stats"]["duplicates"],
            "feedback_records": len(saved["memory"]),
            "pressure_after_recovery": saved["body"]["pressure"],
            "restored_feedback_records": len(restored_state["memory"]),
            "actions_reexecuted_on_restore": restored_state["stats"]["accepted"],
            "scope": "Engineering demonstration; no claim of biological fidelity or consciousness.",
        }
    finally:
        release.set()
        kernel.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON evidence file")
    args = parser.parse_args()
    report = run_demo()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_save(args.output, report)
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
