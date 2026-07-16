from agents.base_agent import AgentResult, AgentTask, BaseAgent


class ChatAgent(BaseAgent):
    """General conversational task handler.

    Echoes user messages. When context is provided via task.payload,
    includes active tasks, recent memories, and world model entities
    in the response — enabling cross-session memory chains.
    """
    name = "chat_agent"
    description = "Handle general conversational tasks with context awareness"

    def can_handle(self, task: AgentTask) -> bool:
        return task.kind == "chat"

    def run(self, task: AgentTask) -> AgentResult:
        text = str(task.payload.get("text", "")).strip()
        context = task.payload.get("context")

        if not text:
            content = "[chat_agent] empty input"
            return AgentResult(
                ok=True, agent=self.name, content=content,
                summary=content[:120], meta={"length": 0},
            )

        response = f"[chat_agent] EVA received: {text}"

        # ── embed context if available ──────────────────────
        if context and isinstance(context, dict):
            ctx_summary = context.get("context_summary", "")
            active_tasks = context.get("active_tasks", [])
            memories = context.get("memories", [])
            entities = context.get("recent_entities", [])

            parts = [response, "", "--- Context ---"]

            if active_tasks:
                task_names = [t.get("name", "") for t in active_tasks if t.get("name")]
                parts.append(f"Active tasks ({len(active_tasks)}): " + ", ".join(task_names[:5]))

            if memories:
                snippets = [
                    m.get("content", "")[:80] for m in memories[:5]
                ]
                parts.append(f"Recent memories ({len(memories)}):")
                for i, s in enumerate(snippets, 1):
                    parts.append(f"  {i}. {s}")

            if entities:
                parts.append(f"Known entities ({len(entities)}): " + ", ".join(entities[:8]))

            response = "\n".join(parts)

        return AgentResult(
            ok=True,
            agent=self.name,
            content=response,
            summary=response[:120],
            meta={
                "length": len(text),
                "has_context": bool(context),
                "active_tasks_count": len(context.get("active_tasks", [])) if context else 0,
                "memories_count": len(context.get("memories", [])) if context else 0,
            },
        )
