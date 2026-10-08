import time
from typing import Any, Callable

from agents.base_agent import AgentResult, AgentTask
from agent_os.registry import AgentRegistry
from core.response_review import review_result
from core.tool_execution import tool_execution_scope


class AgentOrchestrator:
    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry
        self.tool_recorder: Any = None

    def execute(
        self,
        agent_name: str,
        task: AgentTask,
        *,
        executor: Any = None,
        token_manager: Any = None,
        token_id: str = "",
    ) -> tuple[AgentResult, int]:
        """Execute an agent task, optionally gated by an executor.

        When executor is provided, it enforces token validation +
        boundary checking + audit logging around the agent call.
        The agent still performs the actual computation — the
        executor only provides the safety gate.
        """
        agent = self.registry.get(agent_name)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_name}")

        return self.execute_delegated(
            agent_name,
            task,
            agent.run,
            executor=executor,
            token_manager=token_manager,
            token_id=token_id,
        )

    def execute_delegated(
        self,
        agent_name: str,
        task: AgentTask,
        run: Callable[[AgentTask], AgentResult],
        *,
        executor: Any = None,
        token_manager: Any = None,
        token_id: str = "",
    ) -> tuple[AgentResult, int]:
        """Apply the same parent-side gate and review to local or remote execution."""

        def scoped_run(task):
            with tool_execution_scope(dict(task.trace_context), self.tool_recorder):
                return run(task)

        # ── executor path: safety gate around agent ─────────
        if executor is not None:
            result, duration_ms = self._execute_gated(
                scoped_run,
                agent_name,
                task,
                executor,
                token_manager,
                token_id,
            )
            return result, duration_ms

        # ── direct path: agent.run() ────────────────────────
        start = time.perf_counter()
        result = scoped_run(task)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return review_result(result), duration_ms

    def execute_stream(
        self,
        agent_name: str,
        task: AgentTask,
        on_token: Callable[[str], None],
    ) -> tuple[AgentResult, int]:
        """Execute an agent task with per-token streaming callback.

        Calls ``agent.run_stream(task, on_token)`` instead of
        ``agent.run(task)``.

        **Safety gate**: agents that require executor enforcement
        (search_agent, coding_agent, docs_agent → file executor)
        are rejected in stream mode. Streaming bypasses the token +
        boundary check, so only the chat agent — which performs no
        filesystem access — is permitted.
        """
        from agent_os.agent_task import AGENT_EXECUTOR_MAP

        agent = self.registry.get(agent_name)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_name}")

        # Only chat_agent (no executor mapping) is safe for streaming.
        # search/coding/docs agents must go through the gated path.
        if agent_name in AGENT_EXECUTOR_MAP:
            return (
                AgentResult(
                    ok=False,
                    agent=agent_name,
                    content=f"[{agent_name}] streaming denied: agent requires executor gating",
                    summary="streaming blocked for file-access agent",
                    meta={
                        "status": "denied",
                        "reason": "streaming blocked for gated agent",
                    },
                ),
                0,
            )

        start = time.perf_counter()
        # Hold model text until review; otherwise a rejected draft has already
        # leaked to SSE/WS clients. No unbounded token buffer is necessary.
        with tool_execution_scope(dict(task.trace_context), self.tool_recorder):
            result = agent.run_stream(task, lambda _token: None)
        result = review_result(result)
        on_token(result.content)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return result, duration_ms

    def _execute_gated(
        self,
        run: Callable[[AgentTask], AgentResult],
        agent_name: str,
        task: AgentTask,
        executor: Any,
        token_manager: Any,
        token_id: str,
    ) -> tuple[AgentResult, int]:
        from agent_os.agent_task import task_to_executor_params

        converted = task_to_executor_params(
            task,
            executor.name,
            task_id=task.trace_context.get("task_id", f"agent_{agent_name}"),
        )
        action = converted["action"]
        params = converted["params"]

        # 1. token check
        if token_manager:
            tok = executor.validate_token(token_manager, token_id)
            if not tok.allowed:
                executor.audit_log.record(
                    executor_type=executor.name,
                    action=action,
                    task_id=converted.get("task_id", ""),
                    token_id=token_id,
                    parameters=params,
                    result_summary=tok.reason,
                    status="denied",
                )
                return (
                    AgentResult(
                        ok=False,
                        agent=agent_name,
                        content=f"[{agent_name}] token denied: {tok.reason}",
                        summary=f"token denied: {tok.reason[:100]}",
                        meta={"status": "denied", "reason": tok.reason},
                    ),
                    0,
                )

        # 2. boundary check
        boundary = executor.check_boundaries(params)
        if not boundary.allowed:
            executor.audit_log.record(
                executor_type=executor.name,
                action=action,
                task_id=converted.get("task_id", ""),
                token_id=token_id,
                parameters=params,
                result_summary=boundary.reason,
                status="denied",
            )
            return (
                AgentResult(
                    ok=False,
                    agent=agent_name,
                    content=f"[{agent_name}] boundary denied: {boundary.reason}",
                    summary=f"boundary denied: {boundary.reason[:100]}",
                    meta={"status": "denied", "reason": boundary.reason},
                ),
                0,
            )

        # 3. execute (agent does the work; executor just audits)
        start = time.perf_counter()
        try:
            agent_result = review_result(run(task))
            duration_ms = int((time.perf_counter() - start) * 1000)
            executor.audit_log.record(
                executor_type=executor.name,
                action=action,
                task_id=converted.get("task_id", ""),
                token_id=token_id,
                parameters=params,
                result_summary=agent_result.summary[:200],
                duration_ms=duration_ms,
                status="success" if agent_result.ok else "error",
            )
            agent_result.meta["executor"] = executor.name
            agent_result.meta["gated"] = True
            return agent_result, duration_ms
        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            executor.audit_log.record(
                executor_type=executor.name,
                action=action,
                task_id=converted.get("task_id", ""),
                token_id=token_id,
                parameters=params,
                result_summary=str(e)[:200],
                duration_ms=duration_ms,
                status="error",
            )
            return (
                AgentResult(
                    ok=False,
                    agent=agent_name,
                    content=f"[{agent_name}] error: {e}",
                    summary=str(e)[:120],
                    meta={"status": "error", "error": str(e)},
                ),
                duration_ms,
            )
