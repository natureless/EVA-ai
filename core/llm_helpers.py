"""Shared LLM helpers for agents — context building, system prompt loading."""

from __future__ import annotations

from typing import Any


def build_context_text(context: dict[str, Any] | None) -> str:
    """Build a context prefix string from the context dict provided by ContextBuilder.

    Extracts active tasks and recent memories into a compact text block
    suitable for prepending to LLM system prompts.
    """
    if not context or not isinstance(context, dict):
        return ""

    parts: list[str] = []
    tasks = context.get("active_tasks", [])
    memories = context.get("memories", [])

    if tasks:
        parts.append(
            "Active tasks: "
            + ", ".join(t.get("name", "") for t in tasks[:3] if t.get("name"))
        )
    if memories:
        parts.append(
            "Recent context: "
            + "; ".join(m.get("content", "")[:80] for m in memories[:2])
        )

    return "\n".join(parts) + "\n" if parts else ""
