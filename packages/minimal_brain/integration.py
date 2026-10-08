"""Adapters from the minimal scheduler to the existing EVA service graph."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from runtime.agent_worker import worker_is_idle


def create_minimal_brain(container: Any, settings: Any) -> Any:
    """Build one scheduler without starting another queue or execution stack."""
    from packages.minimal_brain.kernel import KernelConfig, MinimalBrainKernel

    if settings.enable_mvsc_pipeline:
        raise ValueError("minimal brain and MVSC cannot be enabled together")

    saved = container.snapshot_store.load_latest() or {}
    initial = saved.get("minimal_brain") if isinstance(saved, dict) else None
    processor = container.runtime.processor
    if processor is None:
        raise ValueError("minimal brain requires an injected event processor")

    def resource_probe() -> dict[str, Any]:
        # These are actual queue/worker measurements, not simulated physiology.
        bus = container.event_bus.stats()
        capacity = max(1, int(bus["max_queue_size"]))
        return {
            "pressure": min(1.0, bus["queue_size"] / capacity),
            "pending_events": bus["queue_size"],
            "event_capacity": capacity,
            "agent_worker": dict(container.runtime.worker_backend.stats),
        }

    def model_probe() -> dict[str, Any]:
        # The kernel isolates this observation from its fast scheduling thread.
        # Stable stores retain sole ownership of evidence and persistence rules.
        world = container.world_model.context_projection()
        self_model = container.self_model or {}
        context = container.system_state.get("last_context_summary") or {}
        return {
            "world": {
                "focus": str(world.get("focus", ""))[:160],
                "mode": world.get("mode"),
                "active_tasks": world["active_tasks"],
                "recent_entities": world["recent_entities"],
                "recent_entity_records": world["recent_entity_records"],
                "last_loop_id": world.get("last_loop_id"),
                "counts": world["counts"],
            },
            "self": {
                key: deepcopy(self_model[key])
                for key in (
                    "name",
                    "version",
                    "identity",
                    "capabilities",
                    "limitations",
                    "stability_metrics",
                )
                if key in self_model
            },
            "memory": {
                "session_entries": len(container.tiered_memory.s1.list_all()),
                "last_context": {
                    "memory_items": context.get("memory_items", 0),
                    "active_tasks": context.get("active_tasks", 0),
                    "summary": str(context.get("summary", ""))[:1000],
                },
            },
        }

    def on_snapshot(snapshot: dict[str, Any]) -> None:
        container.system_state["minimal_brain"] = snapshot

    kernel = MinimalBrainKernel(
        event_bus=container.event_bus,
        process_event=lambda event, context: processor.process_event(
            event,
            cognitive_context=context,
        ),
        on_reject=processor.reject_event,
        resource_probe=resource_probe,
        model_probe=model_probe,
        on_snapshot=on_snapshot,
        processor_ready=lambda: worker_is_idle(container.runtime.worker_backend),
        initial_snapshot=initial if isinstance(initial, dict) else None,
        config=KernelConfig(
            queue_capacity=settings.minimal_brain_queue_capacity,
            poll_interval=settings.minimal_brain_poll_sec,
        ),
    )
    try:
        if getattr(settings, "resume_durable_requests", False):
            kernel.prepare_request_resume(
                container.integrations.durable_requests.unclaimed_event
            )
    except BaseException:
        kernel.stop()
        raise
    return kernel
