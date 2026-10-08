"""Request mode travels to real provider payloads; all network calls are mocked."""
from contextlib import contextmanager
import json
import sys
from types import SimpleNamespace

import pytest

from core.chat_mode import configure_chat_llm
from core.llm_adapter import ClaudeAdapter, MockLLM, OpenAIAdapter


@pytest.fixture(autouse=True)
def mode_environment(monkeypatch):
    monkeypatch.setenv("EVA_LLM_REASONING_STYLE", "auto")
    monkeypatch.setenv("EVA_DEEP_MAX_TOKENS", "4096")
    monkeypatch.setenv("EVA_DEEP_TIMEOUT", "90")


@pytest.mark.parametrize("style,model,base", [
    ("deepseek", "deepseek-chat", "https://api.deepseek.com/v1"),
    ("openai", "gpt-5.5", "https://api.openai.com/v1"),
    ("prompt", "gpt-4o-mini", "https://api.openai.com/v1"),
])
@pytest.mark.parametrize("mode", ["normal", "deep"])
def test_provider_payloads_are_request_local(monkeypatch, style, model, base, mode):
    import httpx
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: calls.append(kwargs) or SimpleNamespace(
        status_code=200, json=lambda: {"choices": [{"message": {"content": "answer"}}]}))
    original = OpenAIAdapter(api_key="fixture", model=model, base_url=base, max_tokens=1024, timeout=30)
    configured = configure_chat_llm(original, mode)
    messages = [{"role": "system", "content": "policy"}, {"role": "user", "content": "question"}]
    assert configured.chat(messages) == "answer"
    body = calls[0]["json"]
    assert original.max_tokens == 1024 and original.timeout == 30
    assert not hasattr(original, "reasoning_style")
    assert messages[0]["content"] == "policy"
    assert "Answer mode: " + mode in body["messages"][0]["content"]
    assert configured.mode_info["strategy"] == ("prompt" if style == "prompt" else "native")
    assert body["model"] == model
    assert calls[0]["timeout"] == (90 if mode == "deep" else 30)
    if style == "openai":
        assert body["reasoning_effort"] == ("high" if mode == "deep" else "low")
        assert body["max_completion_tokens"] == (4096 if mode == "deep" else 1024)
        assert "temperature" not in body and "max_tokens" not in body
    elif style == "deepseek":
        assert body["thinking"] == {"type": "enabled" if mode == "deep" else "disabled"}
        assert ("temperature" in body) is (mode == "normal")
    else:
        assert "reasoning_effort" not in body and "thinking" not in body
        assert body["max_tokens"] == (4096 if mode == "deep" else 1024)


def test_normal_after_deep_has_no_parameter_leak():
    original = OpenAIAdapter(api_key="fixture", base_url="https://api.deepseek.com/v1")
    deep = configure_chat_llm(original, "deep")
    normal = configure_chat_llm(original, "normal")
    assert deep.inner._request_body([])["thinking"]["type"] == "enabled"
    assert normal.inner._request_body([])["thinking"]["type"] == "disabled"
    assert deep.inner.max_tokens > normal.inner.max_tokens


def test_stream_sends_mode_but_never_surfaces_private_reasoning(monkeypatch):
    import httpx
    calls = []

    @contextmanager
    def stream(*args, **kwargs):
        calls.append(kwargs)
        lines = ["data: " + json.dumps({"choices": [{"delta": delta}]}) for delta in (
            {"reasoning_content": "private"}, {"content": "public answer"})]
        yield SimpleNamespace(status_code=200, iter_lines=lambda: iter(lines + ["data: [DONE]"]))

    monkeypatch.setattr(httpx, "stream", stream)
    llm = configure_chat_llm(OpenAIAdapter(api_key="fixture", base_url="https://api.deepseek.com/v1"), "deep")
    assert "".join(llm.chat_stream([{"role": "user", "content": "test"}])) == "public answer"
    assert calls[0]["json"]["thinking"]["type"] == "enabled"
    assert calls[0]["json"]["stream"] is True


@pytest.mark.parametrize("mode", ["normal", "deep"])
def test_claude_adaptive_payload_and_text_only_response(monkeypatch, mode):
    calls = []
    messages_api = SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking="private"),
        SimpleNamespace(type="text", text="answer"),
    ]))
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=lambda **kwargs: SimpleNamespace(messages=messages_api)))
    llm = configure_chat_llm(ClaudeAdapter(api_key="fixture", model="claude-sonnet-4-6"), mode)
    assert llm.chat([{"role": "user", "content": "test"}]) == "answer"
    assert calls[0]["thinking"] == {"type": "adaptive"}
    assert calls[0]["output_config"]["effort"] == ("high" if mode == "deep" else "low")
    assert "temperature" not in calls[0]


def test_manual_claude_budget_and_configuration_validation(monkeypatch):
    monkeypatch.setenv("EVA_LLM_REASONING_STYLE", "claude-budget")
    llm = configure_chat_llm(ClaudeAdapter(api_key="fixture"), "deep")
    options = llm.inner._thinking_options()
    assert 1024 <= options["thinking"]["budget_tokens"] < llm.inner.max_tokens
    with pytest.raises(ValueError, match="incompatible"):
        configure_chat_llm(OpenAIAdapter(api_key="fixture"), "deep")
    monkeypatch.setenv("EVA_DEEP_MAX_TOKENS", "1024")
    with pytest.raises(ValueError, match="2048"):
        configure_chat_llm(ClaudeAdapter(api_key="fixture", max_tokens=1024), "deep")


def test_unknown_model_and_missing_key_are_not_mislabeled_native(monkeypatch):
    assert configure_chat_llm(OpenAIAdapter(api_key="fixture", model="custom"), "deep").mode_info["strategy"] == "prompt"
    assert configure_chat_llm(MockLLM(), "deep").mode_info["strategy"] == "mock"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert configure_chat_llm(OpenAIAdapter(model="gpt-5.5"), "deep").mode_info["strategy"] == "mock"
    with pytest.raises(ValueError):
        configure_chat_llm(MockLLM(), "invalid")


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/sync", "/api/chat/stream"])
def test_chat_mode_validation_at_admission(client, path):
    assert client.post(path, json={"text": "test", "mode": "invalid"}).status_code == 422


@pytest.mark.parametrize("mode", ["normal", "deep"])
def test_sync_receipt_retains_requested_and_applied_mode(client, mode):
    response = client.post("/api/chat/sync", json={"text": "你好", "mode": mode})
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["mode"] == mode
    assert receipt["mode_info"] == {"mode": mode, "strategy": "mock", "reasoning_style": "mock"}
    polled = client.get("/api/chat/result/" + receipt["task_id"]).json()
    assert polled["mode_info"] == receipt["mode_info"]


def test_sse_receipt_and_default_are_compatible(client):
    response = client.post("/api/chat/stream", json={"text": "你好", "mode": "deep"})
    result_text = response.text.split("event: result\ndata: ")[1].split("\n\n")[0]
    result = json.loads(result_text)
    assert result["mode"] == result["mode_info"]["mode"] == "deep"
    default = client.post("/api/chat/sync", json={"text": "你好"}).json()
    assert default["mode"] == "normal"
    modes = client.get("/api/chat/modes").json()["modes"]
    assert [item["mode"] for item in modes] == ["normal", "deep"]
    assert all(item["strategy"] == "mock" for item in modes)
