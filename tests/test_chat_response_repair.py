"""Malformed model formatting can recover without weakening receipt review."""
import json

import pytest

from agent_os.orchestrator import AgentOrchestrator
from agent_os.registry import AgentRegistry
from agents.base_agent import AgentTask
from agents.chat_agent import ChatAgent
from core.tool_registry import ToolDef, ToolRegistry


def envelope(message="指数函数是 f(x)=a^x。", **claim_overrides):
    claim = {"text": message, "status": "assistant_inference", "confidence": 0.5,
             "evidence_ids": [], "time_sensitive": False, **claim_overrides}
    return "```eva_response\n" + json.dumps({
        "message": message, "risk_level": "low", "claims": [claim],
    }, ensure_ascii=False) + "\n```"


class StubLLM:
    provider = "test"

    def __init__(self, replies):
        self.replies = iter(replies)
        self.messages = []

    def chat(self, messages):
        self.messages.append([dict(item) for item in messages])
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def chat_stream(self, messages):
        reply = self.chat(messages)
        yield reply[:10]
        yield reply[10:]


def execute(monkeypatch, streaming, replies, tools=None):
    llm = StubLLM(replies)
    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: llm)
    registry = AgentRegistry()
    registry.register(ChatAgent(tools))
    orchestrator = AgentOrchestrator(registry)
    task = AgentTask("chat", {"text": "解释指数函数"})
    if streaming:
        tokens = []
        result, _ = orchestrator.execute_stream("chat_agent", task, tokens.append)
        assert "".join(tokens) == result.content
    else:
        result, _ = orchestrator.execute("chat_agent", task)
    return result, llm


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("broken", [
    "Markdown answer outside the envelope.\n" + envelope(),
    "```eva_response\n{broken",
    envelope(text="A paraphrase that is not in message"),
])
def test_one_formatting_retry_recovers_and_preserves_original_user(monkeypatch, streaming, broken):
    result, llm = execute(monkeypatch, streaming, [broken, envelope()])

    assert result.ok and result.content == "指数函数是 f(x)=a^x。"
    assert result.meta["response_repair"] == {"attempted": True, "succeeded": True}
    assert result.meta["review"]["fact_verified"] is False
    assert len(llm.messages) == 2
    assert llm.messages[1][-1] == {"role": "user", "content": "解释指数函数"}
    assert "Preserve the language requested by the original user" in llm.messages[1][0]["content"]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("second", [
    "Plain text must not bypass rejection",
    "```eva_response\n{still broken",
    envelope(status="verified_fact"),
    envelope(evidence_ids=["forged"]),
    RuntimeError("provider unavailable"),
    '```tool\n{"tool":"read_file","args":{}}\n```',
])
def test_failed_repair_remains_blocked_and_never_executes_tools(monkeypatch, streaming, second):
    tools = ToolRegistry()
    tools.register(ToolDef("read_file", "local", {}, lambda: pytest.fail("must not execute")))
    result, llm = execute(monkeypatch, streaming, ["```eva_response\n{broken", second], tools)

    assert not result.ok
    assert result.meta["error"] == "response_review_failed"
    assert result.meta["response_repair"] == {"attempted": True, "succeeded": False}
    assert result.meta["tool_rounds"] == 0
    assert len(llm.messages) == 2
    assert "```eva_response" not in result.content


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("draft", [
    envelope(status="verified_fact"),
    envelope(evidence_ids=["forged"]),
    envelope(time_sensitive=True),
    envelope(status="unknown", confidence=0.9),
])
def test_evidence_or_semantic_rejections_are_not_retried(monkeypatch, streaming, draft):
    result, llm = execute(monkeypatch, streaming, [draft])

    assert not result.ok
    assert result.meta["response_repair"] is None
    assert len(llm.messages) == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("reply", ["普通文本回答", envelope()])
def test_valid_reply_does_not_add_a_model_call(monkeypatch, streaming, reply):
    result, llm = execute(monkeypatch, streaming, [reply])

    assert result.ok
    assert result.meta["response_repair"] is None
    assert len(llm.messages) == 1
