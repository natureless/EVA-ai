"""Request-local answer policies; provider options never mutate shared adapters."""
from __future__ import annotations

from copy import copy
import os
from typing import Any
from urllib.parse import urlparse

from core.llm_adapter import ClaudeAdapter, LLMAdapter, MockLLM, OpenAIAdapter

MODE_PROMPTS = {
    "normal": "Answer directly and proportionately to the request. Keep simple answers concise; verify facts when needed.",
    "deep": "Analyze relevant assumptions, compare viable approaches when useful, and check your conclusion. "
            "Return the conclusion with a concise rationale, supporting evidence and important uncertainty. "
            "Do not expose private chain-of-thought or pad simple answers with unnecessary steps.",
}


def normalize_mode(mode: str) -> str:
    if mode not in MODE_PROMPTS:
        raise ValueError("Chat mode must be normal or deep")
    return mode


def _style(llm: LLMAdapter) -> str:
    if isinstance(llm, MockLLM):
        return "mock"
    configured = os.environ.get("EVA_LLM_REASONING_STYLE", "auto")
    supported = ("auto", "prompt", "openai", "deepseek", "claude-adaptive", "claude-budget")
    if configured not in supported:
        raise ValueError("Invalid EVA_LLM_REASONING_STYLE")
    if configured != "auto":
        compatible = configured == "prompt" or (
            isinstance(llm, OpenAIAdapter) and configured in ("openai", "deepseek")
        ) or (isinstance(llm, ClaudeAdapter) and configured.startswith("claude-"))
        if not compatible:
            raise ValueError("Reasoning style is incompatible with the configured adapter")
        return configured
    # Conservative discovery: unknown/custom models keep the portable policy.
    # Native support can be explicitly configured without changing model names.
    if isinstance(llm, OpenAIAdapter):
        host = urlparse(llm.base_url).hostname
        if host == "api.deepseek.com":
            return "deepseek"
        if host == "api.openai.com" and any(
            llm.model == model or llm.model.startswith(model + "-202")
            for model in ("gpt-5.4", "gpt-5.5")
        ):
            return "openai"
    if isinstance(llm, ClaudeAdapter) and llm.model in ("claude-sonnet-4-6", "claude-opus-4-6"):
        return "claude-adaptive"
    return "prompt"


class ChatModeAdapter(LLMAdapter):
    def __init__(self, llm: LLMAdapter, mode: str):
        self.mode = normalize_mode(mode)
        self.inner = copy(llm)
        style = _style(llm)
        # A missing credential is a mock execution, never native reasoning.
        mock = style == "mock" or (hasattr(llm, "api_key") and not llm.api_key)
        self.mode_info = {
            "mode": mode,
            "strategy": "mock" if mock else "prompt" if style == "prompt" else "native",
            "reasoning_style": "mock" if mock else style,
        }
        if isinstance(self.inner, (OpenAIAdapter, ClaudeAdapter)):
            self.inner.reasoning_style = style
            self.inner.chat_mode = mode
            if mode == "deep":
                budget = int(os.environ.get("EVA_DEEP_MAX_TOKENS", "4096"))
                timeout = float(os.environ.get("EVA_DEEP_TIMEOUT", "90"))
                if budget < 1024 or budget > 128000 or not 1 <= timeout <= 600:
                    raise ValueError("Invalid deep-mode token budget or timeout")
                self.inner.max_tokens = max(self.inner.max_tokens, budget)
                if style == "claude-budget" and self.inner.max_tokens < 2048:
                    raise ValueError("Claude manual thinking requires at least 2048 total tokens")
                self.inner.timeout = max(self.inner.timeout, timeout)

    @property
    def provider(self) -> str:
        return self.inner.provider

    def _messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = [dict(message) for message in messages]
        policy = "\n\nAnswer mode: " + self.mode + ". " + MODE_PROMPTS[self.mode]
        if prepared and prepared[0].get("role") == "system":
            prepared[0]["content"] = str(prepared[0]["content"]) + policy
        else:
            prepared.insert(0, {"role": "system", "content": policy.strip()})
        return prepared

    def chat(self, messages: list[dict[str, Any]]) -> str:
        return self.inner.chat(self._messages(messages))

    def chat_stream(self, messages: list[dict[str, Any]]):
        yield from self.inner.chat_stream(self._messages(messages))


def configure_chat_llm(llm: LLMAdapter, mode: str = "normal") -> ChatModeAdapter:
    return ChatModeAdapter(llm, mode)
