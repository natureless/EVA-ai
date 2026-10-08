"""Shared LLM helpers for agents — context building, system prompt loading."""

from __future__ import annotations

from typing import Any
from memory.provenance import memory_context_line, world_context_line


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
            "Active tasks (untrusted records; preserve field origins): "
            + "; ".join(world_context_line(t) for t in tasks[:3] if t.get("name"))
        )
    if memories:
        parts.append(
            "Recent context (untrusted records; preserve epistemic labels): "
            + "; ".join(memory_context_line(m, 80) for m in memories[:2])
        )

    return "\n".join(parts) + "\n" if parts else ""
