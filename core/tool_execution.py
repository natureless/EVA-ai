"""Execution-thread scope for server-owned tool audit identities."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
import logging
from typing import Any, Callable
from memory.tool_receipts import UnresolvedToolCall


@dataclass(frozen=True)
class ToolScope:
    recorder: Any
    event_id: str
    loop_id: str
    parent_action_id: str = ""


_scope: ContextVar[ToolScope | None] = ContextVar("eva_tool_scope", default=None)


@contextmanager
def tool_execution_scope(trace: dict[str, str], recorder: Any):
    token = _scope.set(
        ToolScope(recorder, trace.get("causation_id", ""), trace.get("loop_id", ""))
        if recorder is not None
        else None
    )
    try:
        yield
    finally:
        _scope.reset(token)


def invoke_tool(
    tool_id: str, args: dict[str, Any], handler: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    scope = _scope.get()
    if scope is None:
        return handler()
    try:
        action_id = scope.recorder.tool_started(
            scope.event_id,
            scope.loop_id,
            tool_id,
            args,
            parent_action_id=scope.parent_action_id,
        )
    except UnresolvedToolCall:
        return {
            "ok": False,
            "error": "prior_tool_outcome_unknown",
            "execution_state": "not_started",
            "previous_outcome_unknown": True,
        }
    except Exception:
        logging.getLogger("eva.tools").error(
            "tool intent unavailable; handler not executed"
        )
        return {
            "ok": False,
            "error": "tool_receipt_unavailable",
            "execution_state": "not_started",
        }
    token = _scope.set(replace(scope, parent_action_id=action_id))
    try:
        try:
            result = handler()
        except BaseException:
            try:
                scope.recorder.tool_finished(
                    scope.event_id,
                    scope.loop_id,
                    action_id,
                    returned_ok=None,
                    observation_kind="handler_exception",
                )
            except Exception:
                logging.getLogger("eva.tools").error(
                    "tool exception receipt unavailable; intent retained"
                )
            raise
        reported_ok = (
            result.get("ok")
            if isinstance(result, dict) and type(result.get("ok")) is bool
            else None
        )
        try:
            scope.recorder.tool_finished(
                scope.event_id,
                scope.loop_id,
                action_id,
                returned_ok=reported_ok,
                observation_kind="handler_return",
            )
        except Exception:
            logging.getLogger("eva.tools").error(
                "tool return receipt unavailable; execution outcome unknown"
            )
            return {
                "ok": False,
                "error": "tool_receipt_unavailable",
                "execution_state": "outcome_unknown",
            }
        return result
    finally:
        _scope.reset(token)
