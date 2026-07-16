from agents.base_agent import AgentResult, AgentTask, BaseAgent


class ChatAgent(BaseAgent):
    """General conversational task handler.
    
    Echoes user messages and provides confirmation of receipt.
    Serves as the default agent for general chat interactions.
    """
    name = "chat_agent"
    description = "Handle general conversational tasks"

    def can_handle(self, task: AgentTask) -> bool:
        """Chat agent handles all chat-type tasks."""
        return task.kind == "chat"

    def run(self, task: AgentTask) -> AgentResult:
        """Process a chat task and generate response.
        
        Args:
            task: Chat task with text payload
            
        Returns:
            Result with response content and metadata
        """
        text = str(task.payload.get("text", "")).strip()

        if not text:
            content = "[chat_agent] empty input"
        else:
            content = f"[chat_agent] EVA received: {text}"

        return AgentResult(
            ok=True,
            agent=self.name,
            content=content,
            summary=content[:120],
            meta={"length": len(text)},
        )
