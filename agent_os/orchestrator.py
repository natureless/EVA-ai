import time
from typing import Any, Callable

from agents.base_agent import AgentResult, AgentTask
from agent_os.registry import AgentRegistry


class AgentOrchestrator:
    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

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

        # ── executor path: safety gate around agent ─────────
        if executor is not None:
            return self._execute_gated(
                agent, agent_name, task, executor, token_manager, token_id,
            )

        # ── direct path: agent.run() ────────────────────────
        start = time.perf_counter()
        result = agent.run(task)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return result, duration_ms

    def execute_stream(
        self,
        agent_name: str,
        task: AgentTask,
        on_token: Callable[[str], None],
    ) -> tuple[AgentResult, int]:
        """Execute an agent task with per-token streaming callback.

        Calls ``agent.run_stream(task, on_token)`` instead of
        ``agent.run(task)``. Executor gating is NOT supported in
        stream mode — streaming bypasses the executor safety gate.
        """
        agent = self.registry.get(agent_name)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_name}")

        start = time.perf_counter()
        result = agent.run_stream(task, on_token)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return result, duration_ms

    def _execute_gated(
        self, agent: Any, agent_name: str, task: AgentTask,
        executor: Any, token_manager: Any, token_id: str,
    ) -> tuple[AgentResult, int]:
        from agent_os.agent_task import task_to_executor_params

        converted = task_to_executor_params(
            task, executor.name, task_id=f"agent_{agent_name}"
        )
        action = converted["action"]
        params = converted["params"]

        # 1. token check
        if token_manager:
            tok = executor.validate_token(token_manager, token_id)
            if not tok.allowed:
                executor.audit_log.record(
                    executor_type=executor.name, action=action,
                    task_id=converted.get("task_id", ""),
                    token_id=token_id, parameters=params,
                    result_summary=tok.reason, status="denied",
                )
                return (
                    AgentResult(
                        ok=False, agent=agent_name,
                        content=f"[{agent_name}] token denied: {tok.reason}",
                        summary=f"token denied: {tok.reason[:100]}",
                        meta={"status": "denied", "reason": tok.reason},
                    ), 0,
                )

        # 2. boundary check
        boundary = executor.check_boundaries(params)
        if not boundary.allowed:
            executor.audit_log.record(
                executor_type=executor.name, action=action,
                task_id=converted.get("task_id", ""),
                token_id=token_id, parameters=params,
                result_summary=boundary.reason, status="denied",
            )
            return (
                AgentResult(
                    ok=False, agent=agent_name,
                    content=f"[{agent_name}] boundary denied: {boundary.reason}",
                    summary=f"boundary denied: {boundary.reason[:100]}",
                    meta={"status": "denied", "reason": boundary.reason},
                ), 0,
            )

        # 3. execute (agent does the work; executor just audits)
        start = time.perf_counter()
        try:
            agent_result = agent.run(task)
            duration_ms = int((time.perf_counter() - start) * 1000)
            executor.audit_log.record(
                executor_type=executor.name, action=action,
                task_id=converted.get("task_id", ""),
                token_id=token_id, parameters=params,
                result_summary=agent_result.summary[:200],
                duration_ms=duration_ms, status="success",
            )
            agent_result.meta["executor"] = executor.name
            agent_result.meta["gated"] = True
            return agent_result, duration_ms
        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            executor.audit_log.record(
                executor_type=executor.name, action=action,
                task_id=converted.get("task_id", ""),
                token_id=token_id, parameters=params,
                result_summary=str(e)[:200], duration_ms=duration_ms,
                status="error",
            )
            return (
                AgentResult(
                    ok=False, agent=agent_name,
                    content=f"[{agent_name}] error: {e}",
                    summary=str(e)[:120],
                    meta={"status": "error", "error": str(e)},
                ), duration_ms,
            )
