"""Agent task to executor parameter conversion.

Maps agent task kinds to executor actions and parameters,
enabling agents that access external resources (filesystem, code
execution) to route through the executor framework for token
validation, boundary checking, and audit logging.
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import AgentTask

# ── Agent → Executor mapping ───────────────────────────────

AGENT_EXECUTOR_MAP: dict[str, str] = {
    "search_agent": "file",
    "coding_agent": "file",
    "docs_agent": "file",
}


# ── Task conversion ────────────────────────────────────────

def task_to_executor_params(
    task: AgentTask,
    executor_type: str,
    *,
    task_id: str = "",
) -> dict[str, Any]:
    """Convert an AgentTask into executor.execute(action, params) args."""
    if executor_type == "file":
        return _search_or_code_to_file(task, task_id=task_id)
    if executor_type == "comms":
        return _chat_to_comms(task, task_id=task_id)
    return {
        "action": "agent_task",
        "params": {"kind": task.kind, **task.payload},
        "task_id": task_id,
    }


def _search_or_code_to_file(task: AgentTask, *, task_id: str) -> dict[str, Any]:
    payload = task.payload or {}

    if task.kind == "search":
        query = payload.get("query") or payload.get("text") or ""
        root = payload.get("root") or "."
        return {
            "action": "search",
            "params": {
                "path": root,
                "query": query,
                "kind": "search",
            },
            "task_id": task_id,
        }

    if task.kind == "code":
        file_path = payload.get("path") or ""
        return {
            "action": "inspect",
            "params": {
                "path": file_path,
                "kind": "code",
            },
            "task_id": task_id,
        }

    # docs/summarize/chat: text-only, no fs access needed — use cwd as safe default
    if task.kind in ("summarize", "docs", "chat", "read"):
        return {
            "action": "inspect",
            "params": {
                "path": payload.get("path") or ".",
                "kind": task.kind,
                "text": payload.get("text", ""),
            },
            "task_id": task_id,
        }

    return {
        "action": "read",
        "params": {"path": payload.get("path", ""), **payload},
        "task_id": task_id,
    }


def _chat_to_comms(task: AgentTask, *, task_id: str) -> dict[str, Any]:
    payload = task.payload or {}
    text = payload.get("text") or payload.get("content") or ""
    return {
        "action": "log",
        "params": {
            "message": text,
            "level": "info",
            "kind": task.kind,
        },
        "task_id": task_id,
    }
