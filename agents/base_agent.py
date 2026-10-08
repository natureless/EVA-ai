from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class AgentTask:
    """Represents a task to be executed by an agent.
    
    Attributes:
        kind: Task type identifier (e.g., 'chat', 'search', 'code')
        payload: Task-specific data and parameters
    """
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    # Set by Core, separately from agent/model payload data.
    trace_context: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind or not isinstance(self.kind, str):
            raise ValueError("Task kind must be a non-empty string")
        if not isinstance(self.payload, dict):
            raise ValueError("Task payload must be a dictionary")


@dataclass
class AgentResult:
    """Represents the result of an agent execution.
    
    Attributes:
        ok: Whether execution succeeded
        agent: Name of the agent that produced this result
        content: Full output/response content
        summary: Condensed summary (typically ~120 chars)
        meta: Additional metadata about the execution
    """
    ok: bool
    agent: str
    content: str
    summary: str
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.agent or not isinstance(self.agent, str):
            raise ValueError("Agent name must be a non-empty string")


class BaseAgent(ABC):
    """Abstract base class for all agents in the EVA system.
    
    Each agent should implement can_handle and run methods to define
    which task types it can process and how to execute them.
    """
    name: str = "base_agent"
    description: str = "abstract base agent"

    @abstractmethod
    def can_handle(self, task: AgentTask) -> bool:
        """Check if this agent can handle the given task.
        
        Args:
            task: The task to evaluate
            
        Returns:
            True if this agent can handle the task
        """
        raise NotImplementedError

    @abstractmethod
    def run(self, task: AgentTask) -> AgentResult:
        """Execute the task.

        Args:
            task: The task to execute

        Returns:
            Result object with execution outcome

        Raises:
            Exception: May raise various exceptions on failure
        """
        raise NotImplementedError

    def run_stream(self, task: AgentTask, on_token: Callable[[str], None]) -> AgentResult:
        """Execute task with per-token streaming via callback.

        The default implementation calls ``run()`` and emits the full
        result through ``on_token`` once. Override in agents that can
        produce incremental tokens (e.g. via an LLM streaming API).

        Args:
            task: The task to execute
            on_token: Called for each token (or once with full content)

        Returns:
            Result object with execution outcome
        """
        result = self.run(task)
        on_token(result.content)
        return result
