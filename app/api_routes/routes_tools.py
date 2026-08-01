"""Tools API routes — expose the tool registry to API consumers."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/tools")
def list_tools(request: Request) -> dict[str, object]:
    """List all available tools with their schemas."""
    container = request.app.state.container
    tool_registry = getattr(container, "tool_registry", None)

    if tool_registry is None:
        return {"tools": [], "count": 0}

    tools = []
    for tool in tool_registry.list_all():
        tools.append({
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        })

    return {"tools": tools, "count": len(tools)}


@router.post("/api/tools/call")
def call_tool(request: Request, payload: dict[str, object]) -> dict[str, object]:
    """Call a tool directly (for debugging and testing).

    Request body: {"tool": "search_files", "args": {"query": "hello"}}
    """
    from core.tool_registry import execute_tool, format_tool_result

    container = request.app.state.container
    tool_registry = getattr(container, "tool_registry", None)

    if tool_registry is None:
        return {"ok": False, "error": "tool registry not available"}

    tool_name = str(payload.get("tool", ""))
    tool_args = payload.get("args", {})

    if not isinstance(tool_args, dict):
        return {"ok": False, "error": "args must be a dict"}

    result = execute_tool(tool_name, tool_args, tool_registry)
    formatted = format_tool_result(tool_name, result)

    return {
        "ok": result.get("ok", False),
        "tool": tool_name,
        "result": result,
        "formatted": formatted,
    }


@router.get("/api/tools/stats")
def tool_stats(request: Request) -> dict[str, object]:
    """工具注册表统计 — 可用工具数和分类。"""
    container = request.app.state.container
    tool_registry = getattr(container, "tool_registry", None)

    if tool_registry is None:
        return {"tools": [], "count": 0}

    tools = tool_registry.list_all()
    categories = {
        "filesystem": ["search_files", "read_file", "list_directory"],
        "execution": ["run_code"],
        "network": ["web_fetch", "browse_web", "web_search"],
        "memory": ["search_memory", "ingest_document"],
    }

    tool_names = [t.name for t in tools]
    by_category = {
        cat: [n for n in tool_names if n in cat_tools]
        for cat, cat_tools in categories.items()
    }

    return {
        "count": len(tools),
        "names": tool_names,
        "by_category": by_category,
    }
