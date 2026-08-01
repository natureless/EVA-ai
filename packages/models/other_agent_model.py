"""Other-Agent Model — 追踪外部 Agent 的模型。

协议中定义了 OtherAgentModelProtocol，此模块提供基础实现:
- 记录观察到的外部 Agent 行为
- 区分自身行动 vs 外部 Agent 行动
- 为多 Agent 协作场景提供基础
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class OtherAgentModel:
    """追踪外部 Agent 的简化模型。

    在多 Agent 场景中，EVA 需要区分:
    - 自己的行动
    - 用户的操作
    - 其他 Agent (如 GitHub bot、CI/CD) 的行为
    """

    def __init__(self, max_agents: int = 20) -> None:
        self._agents: dict[str, dict[str, Any]] = {}
        self._max_agents = max_agents

    def observe(
        self,
        agent_id: str,
        agent_type: str,
        action: str,
        confidence: float = 0.5,
    ) -> None:
        """记录观察到的外部 Agent 行为。"""
        if agent_id not in self._agents:
            if len(self._agents) >= self._max_agents:
                oldest = min(self._agents.keys(), key=lambda k: self._agents[k].get("last_seen", ""))
                del self._agents[oldest]

            self._agents[agent_id] = {
                "agent_id": agent_id,
                "agent_type": agent_type,
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "observations": [],
                "reliability": 0.5,
            }

        entry = {
            "action": action,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "confidence": confidence,
        }
        self._agents[agent_id]["observations"].append(entry)
        self._agents[agent_id]["last_seen"] = entry["observed_at"]

        # 保留最近 50 条观察
        if len(self._agents[agent_id]["observations"]) > 50:
            self._agents[agent_id]["observations"] = self._agents[agent_id]["observations"][-50:]

    def is_self(self, agent_id: str) -> bool:
        """判断是否为自身。"""
        return agent_id == "self" or agent_id.startswith("eva-")

    def list_known(self) -> list[dict[str, Any]]:
        """列出所有已知的外部 Agent。"""
        return [
            {
                "agent_id": aid,
                "type": a["agent_type"],
                "observations": len(a["observations"]),
                "last_seen": a.get("last_seen", ""),
            }
            for aid, a in self._agents.items()
        ]

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "known_agents": len(self._agents),
            "total_observations": sum(len(a["observations"]) for a in self._agents.values()),
        }
