"""ExecutorToolAdapter — 将现有 Executor 适配为 MVSC ToolAdapterProtocol。

包装 core/executor.py 的五类执行器 (File/Code/Browser/API/Comms)，
提供统一的 ToolAdapterProtocol 接口:
- validate(ToolRequest) → 检查参数和边界
- execute(ToolRequest) → 执行并返回 ToolResult

与现有代码的关系:
- 现有 Executor 保持不变的继续使用
- 此适配器供 MVSC Pipeline 的 _execute_action 阶段使用
- Token 管理由现有 PolicyEngine.TokenManager 处理
"""

from __future__ import annotations

import logging
import time
from typing import Any

from packages.contracts.protocols import (
    ToolRequest,
    ToolResult,
)

logger = logging.getLogger("eva.tools.executor_adapter")


class ExecutorToolAdapter:
    """将现有 Executor 适配为 ToolAdapterProtocol。

    Usage::

        file_exec = FileExecutor(audit_log, config)
        adapter = ExecutorToolAdapter("file", file_exec, token_manager)

        req = ToolRequest(tool_id="file", parameters={"action": "read", ...})
        await adapter.validate(req)
        result = await adapter.execute(req)
    """

    def __init__(
        self,
        tool_id: str,
        executor: Any,  # BaseExecutor subclass
        token_manager: Any = None,  # PolicyEngine.TokenManager
    ) -> None:
        self.tool_id = tool_id
        self._executor = executor
        self._token_manager = token_manager

    # ── ToolAdapterProtocol ──────────────────────────────────

    async def validate(self, request: ToolRequest) -> None:
        """验证工具调用请求。

        现有 Executor 内部已有:
        - Token 验证 (via validate_token)
        - 边界检查 (via check_boundaries)

        这里做额外的参数 schema 校验。
        """
        if request.tool_id != self.tool_id:
            raise ValueError(
                f"Tool ID mismatch: expected {self.tool_id}, got {request.tool_id}"
            )

        # 基本参数检查
        if not request.parameters:
            raise ValueError("Tool parameters cannot be empty")

        # Executor 内部的 validate + boundary check 在 execute 时进行
        # 这里只做预检查

    async def execute(self, request: ToolRequest) -> ToolResult:
        """执行工具调用。

        委托给现有 Executor.execute()，转换返回格式。
        """
        start = time.perf_counter()

        try:
            # 提取 action 和 params
            action = request.parameters.get("action", "")
            params = request.parameters.get("params", request.parameters)

            # 调用现有 Executor
            raw_result = self._executor.execute(
                action=action,
                params=params,
                task_id=request.parameters.get("task_id", ""),
                token_id=request.token_id or "",
                token_manager=self._token_manager,
            )

            duration_ms = int((time.perf_counter() - start) * 1000)

            # 收集副作用信息
            side_effects: list[str] = []
            if raw_result.get("status") == "success":
                side_effects.append(f"{self.tool_id}:{action}")
            if raw_result.get("files_affected"):
                side_effects.extend(raw_result["files_affected"])

            return ToolResult(
                ok=raw_result.get("ok", False),
                data=raw_result,
                error=raw_result.get("error"),
                duration_ms=duration_ms,
                side_effects=side_effects,
            )

        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.exception("executor adapter error for %s: %s", self.tool_id, e)
            return ToolResult(
                ok=False,
                data={},
                error=str(e),
                duration_ms=duration_ms,
                side_effects=[],
            )


# ═══════════════════════════════════════════════════════════════
# 批量适配器工厂
# ═══════════════════════════════════════════════════════════════

def create_executor_adapters(
    executors: dict[str, Any],
    token_manager: Any = None,
) -> dict[str, ExecutorToolAdapter]:
    """从现有 executors dict 创建 MVSC 适配器映射。

    Args:
        executors: {"file": FileExecutor(...), "code": CodeExecutor(...), ...}
        token_manager: PolicyEngine.TokenManager 实例

    Returns:
        {"file": ExecutorToolAdapter(...), "code": ExecutorToolAdapter(...), ...}
    """
    adapters: dict[str, ExecutorToolAdapter] = {}

    for name, executor in executors.items():
        # 跳过 playwright_browser (特殊处理)
        if name == "playwright_browser":
            continue

        adapters[name] = ExecutorToolAdapter(
            tool_id=name,
            executor=executor,
            token_manager=token_manager,
        )

    return adapters


# ═══════════════════════════════════════════════════════════════
# 工具注册表 → MVSC 适配
# ═══════════════════════════════════════════════════════════════

class ToolRegistryAdapter:
    """将现有 ToolRegistry 适配为 MVSC 可查询的工具目录。

    提供:
    - 按名称查找工具适配器
    - 列出所有可用工具及其参数 schema
    - 与现有 ToolRegistry + Executor 共存
    """

    def __init__(
        self,
        tool_registry: Any = None,  # core.tool_registry.ToolRegistry
        executor_adapters: dict[str, ExecutorToolAdapter] | None = None,
    ) -> None:
        self._registry = tool_registry
        self._executors = executor_adapters or {}

    def get_tool(self, name: str) -> ExecutorToolAdapter | None:
        """获取工具适配器。"""
        return self._executors.get(name)

    def list_tools(self) -> list[dict[str, Any]]:
        """列出所有可用工具及其 schema。"""
        tools: list[dict[str, Any]] = []

        # 从 ToolRegistry 获取工具定义
        if self._registry:
            for tool_def in self._registry.list_all():
                tools.append({
                    "name": tool_def.name,
                    "description": tool_def.description,
                    "parameters": tool_def.parameters,
                    "has_executor": tool_def.name in self._executors,
                })

        return tools

    def tool_names(self) -> list[str]:
        """返回所有工具名称。"""
        names: list[str] = []
        if self._registry:
            names = self._registry.list_names()
        return names
