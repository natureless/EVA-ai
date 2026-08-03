from threading import RLock

from agents.base_agent import BaseAgent


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}
        self._lock = RLock()

    def register(self, agent: BaseAgent, *, replace: bool = False) -> None:
        with self._lock:
            if agent.name in self._agents and not replace:
                raise ValueError(f"Agent already registered: {agent.name}")
            self._agents[agent.name] = agent

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._agents.pop(name, None) is not None

    def get(self, name: str) -> BaseAgent | None:
        with self._lock:
            return self._agents.get(name)

    def list_agents(self) -> list[str]:
        with self._lock:
            return sorted(self._agents.keys())
