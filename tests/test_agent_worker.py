from __future__ import annotations

import threading
from typing import Any, Callable

from agents.base_agent import AgentResult, AgentTask
from runtime.agent_worker import ThreadAgentWorkerBackend


class FakeOrchestrator:
    def execute(
        self,
        agent_name: str,
        task: AgentTask,
        **_: Any,
    ) -> tuple[AgentResult, int]:
        return self._result(agent_name, task.payload["text"]), 7

    def execute_stream(
        self,
        agent_name: str,
        task: AgentTask,
        on_token: Callable[[str], None],
    ) -> tuple[AgentResult, int]:
        on_token("hello")
        return self._result(agent_name, task.payload["text"]), 9

    @staticmethod
    def _result(agent_name: str, content: str) -> AgentResult:
        return AgentResult(
            ok=True,
            agent=agent_name,
            content=content,
            summary=content,
        )


def test_thread_worker_executes_and_reports_stats() -> None:
    worker = ThreadAgentWorkerBackend(FakeOrchestrator(), max_workers=2)
    try:
        result, elapsed_ms = worker.execute(
            "chat_agent",
            AgentTask(kind="chat", payload={"text": "ready"}),
        )

        assert result.ok is True
        assert result.content == "ready"
        assert elapsed_ms == 7
        assert worker.stats["submitted"] == 1
        assert worker.stats["completed"] == 1
    finally:
        worker.shutdown()


def test_thread_worker_streams_through_callback() -> None:
    worker = ThreadAgentWorkerBackend(FakeOrchestrator())
    tokens: list[str] = []
    try:
        result, elapsed_ms = worker.execute_stream(
            "chat_agent",
            AgentTask(kind="chat", payload={"text": "done"}),
            tokens.append,
        )

        assert result.content == "done"
        assert elapsed_ms == 9
        assert tokens == ["hello"]
    finally:
        worker.shutdown()


def test_thread_worker_honors_preexisting_stop_signal() -> None:
    worker = ThreadAgentWorkerBackend(FakeOrchestrator())
    stop_event = threading.Event()
    stop_event.set()
    try:
        result, elapsed_ms = worker.execute(
            "chat_agent",
            AgentTask(kind="chat", payload={"text": "ignored"}),
            stop_event=stop_event,
        )

        assert result.ok is False
        assert result.meta["status"] == "cancelled"
        assert elapsed_ms == 0
    finally:
        worker.shutdown()
