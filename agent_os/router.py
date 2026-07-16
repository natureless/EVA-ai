from agents.base_agent import AgentTask
from agent_os.registry import AgentRegistry


class AgentRouter:
    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

    def route(self, preferred_agent: str, task: AgentTask) -> str:
        agent = self.registry.get(preferred_agent)
        if agent and agent.can_handle(task):
            return preferred_agent

        for name in self.registry.list_agents():
            candidate = self.registry.get(name)
            if candidate and candidate.can_handle(task):
                return name

        # safe fallback: pick first agent, or return the preferred name
        # (orchestrator will surface the error if it's missing)
        agents = self.registry.list_agents()
        return agents[0] if agents else preferred_agent
