from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("eva.api.agents")

router = APIRouter()


class AgentRegistration(BaseModel):
    """Payload for registering a new agent at runtime."""
    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(default="", max_length=500)
    kind: str = Field(default="chat", pattern="^(chat|search|coding|docs|custom)$")


@router.get("/api/agents")
def list_agents(request: Request) -> dict[str, Any]:
    """List all registered agents."""
    registry = request.app.state.container.registry
    agents = []
    for name in registry.list_agents():
        agent = registry.get(name)
        agents.append({
            "name": name,
            "description": getattr(agent, "description", ""),
            "can_handle": getattr(agent, "can_handle", None) is not None,
        })
    return {"agents": agents, "count": len(agents)}


@router.post("/api/agents/register")
def register_agent(payload: AgentRegistration, request: Request) -> dict[str, Any]:
    """Register a new agent at runtime.

    Currently supports registering additional chat agents with custom
    system prompts. For full custom agent logic, use the plugin system.
    """
    container = request.app.state.container
    registry = container.registry

    if registry.get(payload.name) is not None:
        raise HTTPException(status_code=409, detail=f"agent '{payload.name}' already exists")

    try:
        from agents.chat_agent import ChatAgent

        agent = ChatAgent(tool_registry=getattr(container, "tool_registry", None))
        # Override the name for routing purposes
        agent.name = payload.name
        agent.description = payload.description

        registry.register(agent)
        logger.info("hot-registered agent: %s (kind=%s)", payload.name, payload.kind)

        return {
            "ok": True,
            "agent": payload.name,
            "kind": payload.kind,
            "total_agents": len(registry.list_agents()),
        }
    except Exception as e:
        logger.exception("agent registration failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/agents/{name}")
def unregister_agent(name: str, request: Request) -> dict[str, Any]:
    """Remove a dynamically registered agent.

    Built-in agents (chat_agent, search_agent, coding_agent, docs_agent)
    cannot be removed.
    """
    builtins = {"chat_agent", "search_agent", "coding_agent", "docs_agent"}
    if name in builtins:
        raise HTTPException(status_code=403, detail=f"cannot unregister built-in agent: {name}")

    container = request.app.state.container
    registry = container.registry

    if registry.get(name) is None:
        raise HTTPException(status_code=404, detail=f"agent '{name}' not found")

    # The registry doesn't have an unregister method — add one
    if hasattr(registry, '_agents'):
        del registry._agents[name]
        logger.info("unregistered agent: %s", name)
        return {"ok": True, "agent": name, "total_agents": len(registry.list_agents())}
    else:
        raise HTTPException(status_code=500, detail="registry does not support unregistration")
