"""LLM adapter — provider-agnostic interface for OpenAI and Claude APIs.

Supports:
- OpenAI: gpt-4o-mini, gpt-4o, etc. (via OPENAI_API_KEY)
- Claude: claude-sonnet-4-6, claude-haiku-4-5, etc. (via ANTHROPIC_API_KEY)
- Mock: deterministic echo for testing (when no API key configured)

Usage::

    llm = get_llm()  # auto-detects from env
    reply = llm.chat(messages=[
        {"role": "system", "content": "You are EVA."},
        {"role": "user", "content": "Hello"},
    ])
"""

import json
import os
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("eva.llm")


class LLMAdapter(ABC):
    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        ...

    @property
    @abstractmethod
    def provider(self) -> str:
        ...


# ── Mock (no-API-key fallback) ──────────────────────────────

class MockLLM(LLMAdapter):
    """Deterministic echo — used when no API key is configured."""

    def chat(self, messages: list[dict]) -> str:
        last = messages[-1]["content"] if messages else ""

        context_hint = ""
        for m in messages:
            text = m.get("content", "")
            if "Active tasks" in text or "Current context" in text:
                context_hint = " [context-aware]"
                break

        if context_hint:
            return f"[EVA mock{context_hint}] received: {last[:200]}"
        return f"[EVA mock{context_hint}] {last[:200]}"

    @property
    def provider(self) -> str:
        return "mock"


# ── OpenAI Adapter ──────────────────────────────────────────

class OpenAIAdapter(LLMAdapter):
    def __init__(
        self,
        api_key: str = "",
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("EVA_LLM_MODEL", "gpt-4o-mini")
        self.base_url = base_url
        self.timeout = timeout

    def chat(self, messages: list[dict]) -> str:
        if not self.api_key:
            logger.warning("OPENAI_API_KEY not set — falling back to mock")
            return MockLLM().chat(messages)

        try:
            import httpx
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": 1024,
                    "temperature": 0.7,
                },
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            logger.error("OpenAI API error %d: %s", resp.status_code, resp.text[:500])
            return f"[EVA] API error {resp.status_code}"
        except Exception as e:
            logger.error("OpenAI request failed: %s", e)
            return f"[EVA] LLM unavailable: {e}"

    @property
    def provider(self) -> str:
        return f"openai/{self.model}"


# ── Claude Adapter ──────────────────────────────────────────

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6-20250514"


class ClaudeAdapter(LLMAdapter):
    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("EVA_LLM_MODEL", DEFAULT_ANTHROPIC_MODEL)
        self.timeout = timeout

    def chat(self, messages: list[dict]) -> str:
        if not self.api_key:
            logger.warning("ANTHROPIC_API_KEY not set — falling back to mock")
            return MockLLM().chat(messages)

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)

            # separate system message from user/assistant turns
            system = ""
            user_messages = []
            for m in messages:
                if m["role"] == "system" and not system:
                    system = m["content"]
                else:
                    user_messages.append(m)

            kwargs = {
                "model": self.model,
                "max_tokens": 1024,
                "messages": user_messages,
            }
            if system:
                kwargs["system"] = system

            resp = client.messages.create(**kwargs)
            return resp.content[0].text
        except Exception as e:
            logger.error("Claude API request failed: %s", e)
            return f"[EVA] LLM unavailable: {e}"

    @property
    def provider(self) -> str:
        return f"anthropic/{self.model}"


# ── Factory ─────────────────────────────────────────────────

def get_llm(
    provider: str = "",
    model: str = "",
    api_key: str = "",
) -> LLMAdapter:
    """Auto-detect LLM provider from environment or explicit args.

    Priority:
    1. Explicit provider arg
    2. ANTHROPIC_API_KEY env → Claude
    3. OPENAI_API_KEY env → OpenAI
    4. EVA_LLM_PROVIDER env (claude|openai)
    5. Mock fallback
    """
    if provider:
        provider = provider.lower()
    else:
        provider = os.environ.get("EVA_LLM_PROVIDER", "").lower()

    if not provider:
        if os.environ.get("ANTHROPIC_API_KEY"):
            provider = "claude"
        elif os.environ.get("OPENAI_API_KEY"):
            provider = "openai"

    if provider in ("claude", "anthropic"):
        return ClaudeAdapter(api_key=api_key, model=model)
    if provider in ("openai",):
        return OpenAIAdapter(api_key=api_key, model=model)

    logger.info("no LLM API key configured — using mock (echo mode)")
    return MockLLM()
