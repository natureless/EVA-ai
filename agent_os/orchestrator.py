import time

from agents.base_agent import AgentResult, AgentTask
from agent_os.registry import AgentRegistry


class AgentOrchestrator:
    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

    def execute(self, agent_name: str, task: AgentTask) -> tuple[AgentResult, int]:
        agent = self.registry.get(agent_name)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_name}")

        start = time.perf_counter()
        result = agent.run(task)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return result, duration_ms
