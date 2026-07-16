"""LLM adapter — provider-agnostic interface for OpenAI, Claude, and DeepSeek APIs.

Supports:
- OpenAI: gpt-4o-mini, gpt-4o, etc. (via OPENAI_API_KEY)
- Claude: claude-sonnet-4-6, claude-haiku-4-5, etc. (via ANTHROPIC_API_KEY)
- DeepSeek: deepseek-chat, deepseek-reasoner (via DEEPSEEK_API_KEY)
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

    def chat_stream(self, messages: list[dict]):
        """Yield tokens one at a time. Default: yield entire response at once."""
        yield self.chat(messages)

    @property
    @abstractmethod
    def provider(self) -> str:
        ...


# ── Mock (no-API-key fallback) ──────────────────────────────

class MockLLM(LLMAdapter):
    """Deterministic structured output — used when no API key is configured.

    Produces task-like structured replies that exercise the entity
    extraction pipeline. Real LLM replaces this automatically when
    API keys are configured.
    """

    def chat(self, messages: list[dict]) -> str:
        last = messages[-1]["content"] if messages else ""

        context_hint = ""
        for m in messages:
            text = m.get("content", "")
            if "Active tasks" in text or "Current context" in text:
                context_hint = " [context-aware]"
                break

        base = f"[EVA mock{context_hint}] received: {last[:120]}"

        # produce structured output for task-oriented messages
        task_kw = ("fix", "bug", "deploy", "implement", "add", "create", "build")
        user_lower = last.lower()
        if any(kw in user_lower for kw in task_kw):
            if "implement" in user_lower:
                action = "Implement"
            elif "fix" in user_lower:
                action = "Fix"
            elif "deploy" in user_lower:
                action = "Deploy"
            else:
                action = "Add"
            task_name = last[:80].strip()
            base += (
                f"\n\nI'll help with that. Here's the plan:\n"
                f"- [ ] {action} the feature: {task_name}\n"
                f"- [ ] Write tests for the changes\n"
                f"- [ ] Review, document, and verify\n\n"
                f"Priority: high | Deadline: within 2 days"
            )

        return base

    @property
    def provider(self) -> str:
        return "mock"


# ── OpenAI Adapter ──────────────────────────────────────────

class OpenAIAdapter(LLMAdapter):
    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        timeout: float = 0,
        temperature: float = 0,
        max_tokens: int = 0,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("EVA_LLM_MODEL", "gpt-4o-mini")
        self.base_url = base_url or "https://api.openai.com/v1"
        self.timeout = timeout or float(os.environ.get("EVA_LLM_TIMEOUT", "30"))
        self.temperature = temperature or float(os.environ.get("EVA_LLM_TEMPERATURE", "0.7"))
        self.max_tokens = max_tokens or int(os.environ.get("EVA_LLM_MAX_TOKENS", "1024"))

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
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature,
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

    def chat_stream(self, messages: list[dict]):
        if not self.api_key:
            yield MockLLM().chat(messages)
            return

        try:
            import httpx
            with httpx.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature,
                    "stream": True,
                },
                timeout=self.timeout,
            ) as resp:
                if resp.status_code != 200:
                    yield f"[EVA] API error {resp.status_code}"
                    return
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            return
                        try:
                            import json as _json
                            chunk = _json.loads(data_str)
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except Exception:
                            continue
        except Exception as e:
            logger.error("OpenAI stream failed: %s", e)
            yield f"[EVA] LLM unavailable: {e}"

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
        timeout: float = 0,
        temperature: float = 0,
        max_tokens: int = 0,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("EVA_LLM_MODEL", DEFAULT_ANTHROPIC_MODEL)
        self.timeout = timeout or float(os.environ.get("EVA_LLM_TIMEOUT", "30"))
        self.temperature = temperature or float(os.environ.get("EVA_LLM_TEMPERATURE", "0.7"))
        self.max_tokens = max_tokens or int(os.environ.get("EVA_LLM_MAX_TOKENS", "1024"))

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
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "messages": user_messages,
            }
            if system:
                kwargs["system"] = system

            resp = client.messages.create(**kwargs)
            return resp.content[0].text
        except Exception as e:
            logger.error("Claude API request failed: %s", e)
            return f"[EVA] LLM unavailable: {e}"

    def chat_stream(self, messages: list[dict]):
        if not self.api_key:
            yield MockLLM().chat(messages)
            return

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)

            system = ""
            user_messages = []
            for m in messages:
                if m["role"] == "system" and not system:
                    system = m["content"]
                else:
                    user_messages.append(m)

            kwargs = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "messages": user_messages,
            }
            if system:
                kwargs["system"] = system

            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    yield text
        except Exception as e:
            logger.error("Claude stream failed: %s", e)
            yield f"[EVA] LLM unavailable: {e}"

    @property
    def provider(self) -> str:
        return f"anthropic/{self.model}"


# ── Factory ─────────────────────────────────────────────────

def get_llm(
    provider: str = "",
    model: str = "",
    api_key: str = "",
    temperature: float = 0,
    max_tokens: int = 0,
) -> LLMAdapter:
    """Auto-detect LLM provider from environment or explicit args.

    Priority:
    1. Explicit provider arg
    2. ANTHROPIC_API_KEY env -> Claude
    3. DEEPSEEK_API_KEY env -> DeepSeek (via OpenAI-compatible API)
    4. OPENAI_API_KEY env -> OpenAI
    5. EVA_LLM_PROVIDER env (claude|openai|deepseek)
    6. Mock fallback

    Tuning params (temperature, max_tokens) read from env vars
    EVA_LLM_TEMPERATURE / EVA_LLM_MAX_TOKENS if not explicitly set.
    """
    if provider:
        provider = provider.lower()
    else:
        provider = os.environ.get("EVA_LLM_PROVIDER", "").lower()

    if not provider:
        if os.environ.get("ANTHROPIC_API_KEY"):
            provider = "claude"
        elif os.environ.get("DEEPSEEK_API_KEY"):
            provider = "deepseek"
        elif os.environ.get("OPENAI_API_KEY"):
            provider = "openai"

    if provider in ("claude", "anthropic"):
        return ClaudeAdapter(api_key=api_key, model=model,
                            temperature=temperature, max_tokens=max_tokens)
    if provider == "deepseek":
        return OpenAIAdapter(
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY", ""),
            model=model or os.environ.get("EVA_LLM_MODEL", "deepseek-chat"),
            base_url="https://api.deepseek.com/v1",
            temperature=temperature, max_tokens=max_tokens,
        )
    if provider in ("openai",):
        return OpenAIAdapter(api_key=api_key, model=model,
                            temperature=temperature, max_tokens=max_tokens)

    logger.info("no LLM API key configured — using mock (echo mode)")
    return MockLLM()


def load_system_prompt(agent_name: str, **kwargs) -> str:
    """Load the system prompt template for an agent from config/system_prompt.yaml.

    Falls back to built-in defaults if the file is missing.
    kwargs fill {variable} placeholders in the template.
    """
    try:
        import yaml
        from pathlib import Path
        path = Path("config/system_prompt.yaml")
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            agents = cfg.get("agents", {})
            template = agents.get(agent_name, {}).get("system", "")
            if template:
                for key, val in kwargs.items():
                    template = template.replace("{" + key + "}", str(val))
                return template
    except Exception as e:
        logger.debug("failed to load system prompt template: %s", e)

    # built-in fallbacks
    defaults = {
        "chat": "You are EVA, a persistent cognitive assistant. Be precise, calm, concise.",
        "search": "You are EVA's search agent. Summarize file search results concisely.",
        "coding": "You are EVA's code analysis agent. Summarize code structure concisely.",
        "docs": "You are EVA's document analysis agent. Summarize documents concisely.",
    }
    return defaults.get(agent_name, defaults["chat"])
