"""HTTP event admission follows the selected runtime and EventBus boundary."""

from typing import Any

from fastapi.responses import JSONResponse

from event.event_schema import Event


def publish_event(container: Any, event: Event) -> str | None:
    """Return a rejection reason, or None once the event is admitted."""
    bus = container.event_bus
    if getattr(bus, "accepting", True) is False:
        # Go through publish so rejected admission remains visible in bus stats.
        bus.publish(event)
        return "runtime_stopping"
    runtime = getattr(container, "runtime", None)
    controller = getattr(runtime, "controller", None)
    if controller is not None and controller.accepting is False:
        return "runtime_unavailable"
    if bus.publish(event):
        return None
    return "runtime_stopping" if bus.accepting is False else "event_queue_full"


def admission_rejected(reason: str, **fields: Any) -> JSONResponse:
    details = {
        "runtime_stopping": "EVA is stopping and cannot accept new events. Retry when it is ready.",
        "runtime_unavailable": "EVA's cognitive runtime is unavailable. Retry when it is ready.",
        "event_queue_full": "EVA's event queue is full. Please retry this message shortly.",
        "result_capacity_full": "EVA's result capacity is full. Please retry this message shortly.",
        "durable_request_unavailable": "EVA's request persistence is unavailable. No work was queued.",
    }
    return JSONResponse(
        status_code=503,
        content={
            "accepted": False,
            "error": reason,
            "detail": details[reason],
            **fields,
        },
        headers={"Retry-After": "1"},
    )
