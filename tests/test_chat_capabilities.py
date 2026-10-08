"""Regression coverage for the language/capability mismatch in chat replies."""

import pytest

from agents.base_agent import AgentTask
from agents.chat_agent import ChatAgent
from core.tool_registry import ToolDef, ToolRegistry, create_builtin_tools


class RecordingLLM:
    provider = "test"

    def __init__(self, replies):
        self.replies = iter(replies)
        self.messages = []

    def chat(self, messages):
        self.messages.append([dict(message) for message in messages])
        return next(self.replies)

    def chat_stream(self, messages):
        yield self.chat(messages)


def run_chat(agent, streaming, payload):
    task = AgentTask(kind="chat", payload=payload)
    if not streaming:
        return agent.run(task)
    tokens = []
    result = agent.run_stream(task, tokens.append)
    assert result.content in "".join(tokens)
    return result


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("tools_allowed", [False, True])
def test_request_capabilities_match_execution(monkeypatch, streaming, tools_allowed):
    calls = []
    registry = ToolRegistry()
    registry.register(ToolDef(
        name="web_fetch", description="Fetch a public web page",
        parameters={"type": "object", "properties": {}},
        handler=lambda: calls.append(True) or {"ok": True, "content": "fixture data"},
    ))
    replies = ["已收到。"]
    if tools_allowed:
        replies.insert(0, '```tool\n{"tool": "web_fetch", "args": {}}\n```')
    llm = RecordingLLM(replies)
    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: llm)

    result = run_chat(ChatAgent(registry), streaming, {
        "text": "现在美股行情", "tools_allowed": tools_allowed,
    })

    if not tools_allowed:
        assert result.meta["current_data_guard"] is True
        assert "没有可用的联网或行情工具" in result.content
        assert llm.messages == []
        assert calls == []
        return
    system = llm.messages[0][0]["content"]
    assert ("**web_fetch**" in system) is tools_allowed
    assert ("No tools are available for this request" in system) is not tools_allowed
    assert len(calls) == result.meta["tool_rounds"] == result.meta["tools_available"] == int(tools_allowed)
    assert result.ok


@pytest.mark.parametrize("streaming", [False, True])
def test_direct_authorization_tools_are_not_advertised(monkeypatch, streaming):
    registry = ToolRegistry()
    registry.register(ToolDef(
        name="ingest_document", description="Write a document to memory",
        parameters={}, handler=lambda: pytest.fail("must not execute"),
        requires_user_authorization=True,
    ))
    llm = RecordingLLM(["我是 EVA。"])
    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: llm)

    result = run_chat(ChatAgent(registry), streaming, {"text": "你是谁"})

    assert "ingest_document" not in llm.messages[0][0]["content"]
    assert "No tools are available for this request" in llm.messages[0][0]["content"]
    assert result.meta["tools_available"] == result.meta["tool_rounds"] == 0


@pytest.mark.parametrize("streaming", [False, True])
def test_file_only_chat_does_not_claim_network_or_code(monkeypatch, streaming):
    registry = ToolRegistry()
    for tool in create_builtin_tools():
        registry.register(tool)
    llm = RecordingLLM(["当前无法获取实时行情。"])
    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: llm)

    result = run_chat(ChatAgent(registry), streaming, {"text": "现在美股行情"})

    assert result.meta["current_data_guard"] is True
    assert "无法提供这类问题的实时数据" in result.content
    assert llm.messages == []
    assert result.meta["tools_available"] == len(ChatAgent(registry)._available_tools())
    assert result.meta["tools_available"] > 0
    assert result.meta["tool_rounds"] == 0


@pytest.mark.parametrize("streaming", [False, True])
def test_file_only_chat_advertises_actual_local_tools(monkeypatch, streaming):
    registry = ToolRegistry()
    for tool in create_builtin_tools():
        registry.register(tool)
    llm = RecordingLLM(["可以读取本地文件。"])
    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: llm)

    run_chat(ChatAgent(registry), streaming, {"text": "今天帮我读文件"})

    system = llm.messages[0][0]["content"]
    assert "**read_file**" in system
    assert "**web_fetch**" not in system
    assert "**run_code**" not in system
    assert "run scripts, fetch web pages" not in system
    assert "Local file search and memory search are not web search" in system
    assert "Do not offer to fetch a supplied URL" in system


@pytest.mark.parametrize("streaming", [False, True])
def test_latest_local_file_request_can_execute_read_tool(monkeypatch, streaming):
    calls = []
    registry = ToolRegistry()
    registry.register(ToolDef(
        name="read_file", description="Read a local file", parameters={},
        handler=lambda: calls.append(True) or {"ok": True, "content": "local log fixture"},
    ))
    llm = RecordingLLM(['```tool\n{"tool": "read_file", "args": {}}\n```', "已读取本地日志。"])
    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: llm)

    result = run_chat(ChatAgent(registry), streaming, {"text": "读取最新的本地日志"})

    assert result.ok
    assert calls == [True]
    assert result.meta["tool_rounds"] == 1
    assert result.content == "已读取本地日志。"
    assert not result.meta.get("current_data_guard")


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("question", ["美股是什么", "请用英语回答：你是谁"])
def test_latest_user_language_policy_reaches_both_paths(monkeypatch, streaming, question):
    llm = RecordingLLM(["test response"])
    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: llm)

    run_chat(ChatAgent(), streaming, {
        "text": question,
        "context": {"context_summary": "Earlier assistant reply: I cannot access live prices."},
    })

    messages = llm.messages[0]
    assert messages[-1] == {"role": "user", "content": question}
    system = messages[0]["content"]
    assert "language of the latest user request" in system
    assert "unless the user explicitly" in system
    assert "message and claim text in eva_response" in system


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("question", [
    "解释指数函数",
    "现在解释指数函数",
    "今天帮我读文件",
    "读取最新的本地日志",
    "当前任务有哪些",
    "新闻是什么",
    "实时系统是什么",
    "天气预报的原理是什么",
    "写一则新闻",
    "解释今天的天气现象",
    "2019 年的美股行情如何",
    "今天美股行情：标普500涨1%，请分析这份数据",
    "今天新闻：公司发布新产品。请总结这段文字",
    "Explain the current variable in this code",
    "Explain stock price valuation",
    "Explain what breaking news means",
    "Read today's local log file",
    "How does weather forecasting work?",
    "Analyze these live market quotes: AAPL 100, MSFT 200",
    "What happened to stocks in 2008?",
    "live streaming 是什么意思",
    "请用法语回答：现在美股行情",
])
def test_keyword_overlap_does_not_block_model(monkeypatch, streaming, question):
    llm = RecordingLLM(["model response"])
    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: llm)

    result = run_chat(ChatAgent(), streaming, {"text": question})

    assert result.content == "model response"
    assert not result.meta.get("current_data_guard")
    assert llm.messages[0][-1] == {"role": "user", "content": question}
    assert "For current facts" in llm.messages[0][0]["content"]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("question", [
    "现在美股行情",
    "请帮我查询今天的A股行情？",
    "今日港股行情怎么样？",
    "AAPL股价",
    "当前标普500的当前点位是多少？",
    "今天天气怎么样？",
    "最新新闻有哪些？",
    "latest news",
    "Please show me current stock prices?",
    "live AAPL price",
    "today's weather",
])
def test_clear_live_lookup_without_network_does_not_call_model(monkeypatch, streaming, question):
    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: pytest.fail("must not call model"))

    result = run_chat(ChatAgent(), streaming, {"text": question})

    assert result.ok
    assert result.meta["current_data_guard"] is True
    assert result.meta["tool_rounds"] == result.meta["tools_available"] == 0
    assert result.meta["evidence"] == []


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(("question", "expected"), [
    ("请用英语回答：现在美股行情", "I do not have a network"),
    ("用英文回答：最新新闻", "I do not have a network"),
    ("请用中文回答：latest news", "我目前没有可用的联网或行情工具"),
])
def test_guard_respects_requested_language_and_context_metadata(monkeypatch, streaming, question, expected):
    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: pytest.fail("must not call model"))
    registry = ToolRegistry()
    registry.register(ToolDef(name="read_file", description="local", parameters={},
                              handler=lambda: pytest.fail("must not execute")))

    result = run_chat(ChatAgent(registry), streaming, {
        "text": question,
        "context": {"active_tasks": [{"id": "task"}], "memories": [{"id": "memory"}]},
    })

    assert result.content.startswith(expected)
    assert result.meta["tools_available"] == 1
    assert result.meta["has_context"] is True
    assert result.meta["active_tasks_count"] == result.meta["memories_count"] == 1
    assert result.meta["tool_rounds"] == 0
