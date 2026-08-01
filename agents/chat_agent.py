from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, TYPE_CHECKING

from agents.base_agent import AgentResult, AgentTask, BaseAgent
from core.llm_adapter import get_llm, load_system_prompt

if TYPE_CHECKING:
    from core.llm_adapter import LLMAdapter
    from core.tool_registry import ToolRegistry

logger = logging.getLogger("eva.chat_agent")

# Maximum number of tool-calling rounds before forcing a final answer
MAX_TOOL_ROUNDS = 5

# Tool-use system prompt suffix
TOOL_USE_PROMPT = """
You have access to tools that let you search files, read code, list directories,
run scripts, fetch web pages, and search your memory.

When you need information you don't have:
1. Decide which tool(s) to call
2. Call the tool by responding with a JSON block:
   ```tool
   {"tool": "<tool_name>", "args": {<arguments>}}
   ```
3. The tool result will be provided, then you continue

Rules:
- Use tools when the answer requires reading files, searching, or fetching data
- If you already know the answer, respond directly without calling tools
- After getting tool results, synthesize a clear answer for the user
- You may call multiple tools in sequence, but not more than 5 times
- Always format your final answer with markdown (bullet lists, code blocks, etc.)
- If a tool fails, explain the issue to the user and suggest alternatives
"""


class ChatAgent(BaseAgent):
    """General conversational task handler with LLM, context, and tool-calling.

    Supports:
    - Multi-provider LLM (OpenAI, Claude, DeepSeek, mock)
    - Streaming and non-streaming modes
    - Tool-calling loop: LLM decides → execute tool → feed result → LLM → answer
    - Context injection from memory, world model, and persona
    """

    name = "chat_agent"
    description = "Handle general conversational tasks with LLM + context + tools"

    def __init__(self, tool_registry: ToolRegistry | None = None) -> None:
        """Initialize with optional tool registry.

        Args:
            tool_registry: ToolRegistry for tool-calling. If None, tools are disabled.
        """
        self.tool_registry: ToolRegistry | None = tool_registry

    def can_handle(self, task: AgentTask) -> bool:
        return task.kind == "chat"

    # ── Non-streaming run with tool-calling loop ──────────────

    def run(self, task: AgentTask) -> AgentResult:
        text = str(task.payload.get("text", "")).strip()
        context = task.payload.get("context")

        if not text:
            return AgentResult(
                ok=True, agent=self.name,
                content="[chat_agent] empty input",
                summary="empty input", meta={},
            )

        llm = get_llm()

        # Build system prompt with persona + context + tool instructions
        system = self._build_system(context)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ]

        # ── Tool-calling loop ─────────────────────────────────
        if self.tool_registry and self.tool_registry.list_all():
            reply, tool_rounds = self._tool_loop(llm, messages)
        else:
            reply = llm.chat(messages)
            tool_rounds = 0

        return AgentResult(
            ok=True,
            agent=self.name,
            content=reply,
            summary=reply[:120],
            meta={
                "llm_provider": llm.provider,
                "has_context": bool(context),
                "active_tasks_count": len(context.get("active_tasks", [])) if context else 0,
                "memories_count": len(context.get("memories", [])) if context else 0,
                "tool_rounds": tool_rounds,
                "tools_available": len(self.tool_registry.list_all()) if self.tool_registry else 0,
            },
        )

    # ── Streaming run with tool-calling ───────────────────────

    def run_stream(self, task: AgentTask, on_token: Callable[[str], None]) -> AgentResult:
        """Execute with per-token streaming via LLM chat_stream.

        In streaming mode:
        1. Tool calls emit progress tokens (e.g. "🔍 searching files...")
        2. Tool results are injected, and the LLM continues
        3. The final answer is streamed token-by-token
        """
        text = str(task.payload.get("text", "")).strip()
        context = task.payload.get("context")

        if not text:
            result = AgentResult(
                ok=True, agent=self.name,
                content="[chat_agent] empty input",
                summary="empty input", meta={},
            )
            on_token(result.content)
            return result

        llm = get_llm()
        system = self._build_system(context)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ]

        tool_rounds = 0

        # ── Handle tool calls with progress streaming ──────────
        if self.tool_registry and self.tool_registry.list_all():
            # Run tool loop with progress callbacks
            reply, tool_rounds = self._tool_loop(llm, messages, on_token=on_token)

            # Stream the final answer character-by-character for smooth rendering
            for char in reply:
                on_token(char)

            return AgentResult(
                ok=True,
                agent=self.name,
                content=reply,
                summary=reply[:120],
                meta={
                    "llm_provider": llm.provider,
                    "has_context": bool(context),
                    "active_tasks_count": len(context.get("active_tasks", [])) if context else 0,
                    "memories_count": len(context.get("memories", [])) if context else 0,
                    "tool_rounds": tool_rounds,
                    "tools_available": len(self.tool_registry.list_all()),
                },
            )

        # No tools: stream directly
        full_reply = ""
        for token in llm.chat_stream(messages):
            full_reply += token
            on_token(token)

        return AgentResult(
            ok=True,
            agent=self.name,
            content=full_reply,
            summary=full_reply[:120],
            meta={
                "llm_provider": llm.provider,
                "has_context": bool(context),
                "active_tasks_count": len(context.get("active_tasks", [])) if context else 0,
                "memories_count": len(context.get("memories", [])) if context else 0,
                "tool_rounds": 0,
                "tools_available": 0,
            },
        )

    # ── Tool-calling loop ─────────────────────────────────────

    def _tool_loop(
        self,
        llm: LLMAdapter,
        messages: list[dict[str, Any]],
        on_token: Callable[[str], None] | None = None,
    ) -> tuple[str, int]:
        """Run the tool-calling loop: LLM → parse tool call → execute → feed result.

        Returns (final_reply, tool_rounds).

        When ``on_token`` is provided, tool progress events are emitted:
        - ``[TOOL_START:name]`` at the start of each tool call
        - ``[TOOL_RESULT:name]`` with a brief summary after execution
        - ``[TOOL_ERROR:name]`` if a tool fails

        Uses a simple JSON-block protocol:
        - LLM responds with ```tool { "tool": "...", "args": {...} } ```
        - System parses, executes, and appends result as a tool message
        - Loop continues until LLM produces a final answer or max rounds reached
        """
        from core.tool_registry import execute_tool, format_tool_result

        def _emit(event_type: str, tool_name: str, detail: str = "") -> None:
            if on_token:
                on_token(f"\n[{event_type}:{tool_name}] {detail}\n")

        for round_num in range(MAX_TOOL_ROUNDS):
            # ── LLM call with retry + backoff ──────────────────
            response = None
            last_error = None
            max_retries = _get_llm_max_retries()
            for attempt in range(max_retries + 1):
                try:
                    response = llm.chat(messages)
                    break
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        delay = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                        logger.warning(
                            "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                            attempt + 1, max_retries + 1, delay, e,
                        )
                        import time
                        time.sleep(delay)
                    else:
                        logger.error(
                            "LLM call failed after %d retries in round %d: %s",
                            max_retries + 1, round_num + 1, e,
                        )

            if response is None:
                _emit("TOOL_ERROR", "system", f"LLM error after retries: {last_error}")
                if round_num == 0:
                    return f"[chat_agent] LLM communication error: {last_error}", 0
                messages.append({
                    "role": "user",
                    "content": f"The previous call failed with: {last_error}. Please provide your best answer.",
                })
                try:
                    response = llm.chat(messages)
                except Exception as e2:
                    logger.error("LLM retry also failed: %s", e2)
                    return "[chat_agent] LLM communication error after retry", round_num

            # Try to parse a tool call from the response
            tool_call = self._parse_tool_call(response)

            if tool_call is None:
                # No tool call → this is the final answer
                return response, round_num

            tool_name = tool_call["tool"]
            tool_args = tool_call.get("args", {})

            # ── Fuzzy match tool name ──────────────────────────
            if self.tool_registry:
                available = self.tool_registry.list_names()
                if tool_name not in available:
                    fuzzy = _fuzzy_match_tool_name(tool_name, available)
                    if fuzzy:
                        logger.info(
                            "tool-call fuzzy match: '%s' → '%s'", tool_name, fuzzy,
                        )
                        tool_name = fuzzy

            # ── Coerce argument types ───────────────────────────
            if self.tool_registry:
                tool_def = self.tool_registry.get(tool_name)
                if tool_def:
                    tool_args = _coerce_tool_args(tool_name, tool_args, tool_def.parameters)

            logger.info(
                "tool-call round=%d tool=%s args=%s",
                round_num + 1, tool_name,
                json.dumps(tool_args, ensure_ascii=False)[:200],
            )

            # Emit tool start event for streaming UI
            _emit("TOOL_START", tool_name, self._summarize_tool_args(tool_name, tool_args))

            # Execute the tool
            result = execute_tool(tool_name, tool_args, self.tool_registry)

            if result.get("ok") is False:
                _emit("TOOL_ERROR", tool_name, result.get("error", "unknown error")[:200])
            else:
                _emit("TOOL_RESULT", tool_name, self._summarize_tool_result(tool_name, result))

            formatted = format_tool_result(tool_name, result)

            # Append the assistant's response (with tool call) and tool result
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"Tool result:\n{formatted}\n\nContinue your response based on this result."})

        # Max rounds reached — ask LLM for final answer
        _emit("TOOL_MAX_ROUNDS", "system", f"Reached {MAX_TOOL_ROUNDS} tool call limit")
        messages.append({
            "role": "user",
            "content": "You've reached the maximum number of tool calls. "
                       "Please provide your best answer now with the information you have.",
        })
        final = llm.chat(messages)
        return final, MAX_TOOL_ROUNDS

    # ── Tool call result summarization ─────────────────────────

    @staticmethod
    def _summarize_tool_args(tool_name: str, args: dict[str, Any]) -> str:
        """Create a human-readable summary of tool arguments."""
        if tool_name == "search_files":
            return f'Searching for "{args.get("query", "?")}" in {args.get("path", ".")}'
        elif tool_name == "read_file":
            return f'Reading {args.get("path", "?")}'
        elif tool_name == "list_directory":
            return f'Listing {args.get("path", ".")}'
        elif tool_name == "run_code":
            code = args.get("code", "")
            return f'Running: {code[:80]}{"..." if len(code) > 80 else ""}'
        elif tool_name == "web_fetch":
            return f'Fetching {args.get("url", "?")[:80]}'
        elif tool_name == "search_memory":
            return f'Searching memory for "{args.get("query", "?")}"'
        elif tool_name == "browse_web":
            return f'Browsing {args.get("url", "?")[:80]}'
        elif tool_name == "web_search":
            return f'Searching web for "{args.get("query", "?")}"'
        return json.dumps(args, ensure_ascii=False)[:100]

    @staticmethod
    def _summarize_tool_result(tool_name: str, result: dict[str, Any]) -> str:
        """Create a brief summary of tool execution result."""
        if tool_name == "search_files":
            count = len(result.get("results", []))
            return f"Found {count} file(s)"
        elif tool_name == "read_file":
            size = len(result.get("content", ""))
            return f"Read {size} chars"
        elif tool_name == "list_directory":
            count = len(result.get("entries", []))
            return f"Listed {count} entries"
        elif tool_name == "run_code":
            return f"Exit code: {result.get('exit_code', '?')}"
        elif tool_name == "web_fetch":
            size = len(result.get("content", ""))
            return f"Fetched {size} chars"
        elif tool_name == "search_memory":
            count = len(result.get("results", []))
            return f"Found {count} memory matches"
        elif tool_name == "browse_web":
            return str(result.get("summary", "Browsed page"))
        elif tool_name == "web_search":
            return str(result.get("summary", "Searched web"))
        return "Done"

    # ── Tool call parsing ─────────────────────────────────────

    @staticmethod
    def _parse_tool_call(text: str) -> dict[str, Any] | None:
        """Parse a ```tool JSON block from the LLM response.

        Returns {"tool": str, "args": dict} or None if no tool call found.

        Supports multiple formats (in order of priority):
        1. ```tool\\n{"tool": "...", "args": {...}}\\n```
        2. {"tool": "...", "args": {...}}  (bare JSON)
        3. Truncated JSON recovery (auto-close missing braces)
        4. Fuzzy tool name correction
        """
        import re

        # Try fenced code block format
        fence_match = re.search(r"```tool\s*\n(.*?)\n```", text, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try bare JSON object with "tool" and "args" keys
        json_match = re.search(r'\{"tool"\s*:\s*"[^"]+"\s*,\s*"args"\s*:\s*\{[^}]+\}\}', text)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # Try finding any JSON object with "tool" key
        for match in re.finditer(r'\{[^{}]*"tool"[^{}]*\}', text):
            try:
                obj = json.loads(match.group(0))
                if "tool" in obj:
                    return obj
            except json.JSONDecodeError:
                continue

        # ── Recovery: truncated JSON ──────────────────────────
        # If LLM output was cut off, try to auto-close braces
        truncated = _recover_truncated_json(text)
        if truncated:
            return truncated

        return None

    # ── System prompt builder ─────────────────────────────────

    def _build_system(self, context: dict[str, Any] | None) -> str:
        """Build the system prompt with persona, context, and tool instructions."""
        if context and context.get("persona"):
            p = context["persona"]
            persona_name = p.get("name", "EVA")
            persona_role = p.get("role_definition", "cognitive assistant")
            tone = p.get("tone_style", "precise, calm, concise")
            hard = "\n".join(f"- {c}" for c in p.get("hard_constraints", []))
        else:
            persona_name = "EVA"
            persona_role = "persistent cognitive assistant"
            tone = "precise, calm, concise"
            hard = "- do not fabricate memories\n- do not overclaim certainty"

        ctx_summary = context.get("context_summary", "") if context else ""

        base = load_system_prompt(
            "chat",
            persona_name=persona_name,
            persona_role=persona_role,
            tone_style=tone,
            hard_constraints=hard,
            context_summary=ctx_summary,
        )

        # Append tool instructions if tools are available
        if self.tool_registry and self.tool_registry.list_all():
            tools_desc = self._describe_tools()
            base += "\n\n" + TOOL_USE_PROMPT + "\n\n" + tools_desc

        return base

    def _describe_tools(self) -> str:
        """Build a text description of available tools for the system prompt."""
        if not self.tool_registry:
            return ""

        lines = ["Available tools:"]
        for tool in self.tool_registry.list_all():
            # Extract required params
            required = tool.parameters.get("required", [])
            props = tool.parameters.get("properties", {})
            param_desc = ", ".join(
                f"{k}" + (" (required)" if k in required else "")
                for k in list(props.keys())[:5]
            )
            lines.append(f"- **{tool.name}**: {tool.description}")
            lines.append(f"  Parameters: {param_desc}" if param_desc else "  Parameters: none")
        return "\n".join(lines)


# ── Tool-call fault tolerance helpers ──────────────────────────


def _recover_truncated_json(text: str) -> dict[str, Any] | None:
    """Attempt to recover a truncated JSON tool call by auto-closing braces.

    Handles cases where the LLM output is cut off mid-response, e.g.:
    - ``{"tool": "read_file", "args": {"path": "/d/EV``
    - ``{"tool": "list_directory", "args": {"path``

    Returns a valid parsed dict or None.
    """
    import re

    # Find a pattern that starts like a tool call: {"tool": "...", "args": {...
    match = re.search(
        r'\{\s*"tool"\s*:\s*"([^"]+)"\s*,\s*"args"\s*:\s*(\{.*)',
        text, re.DOTALL,
    )
    if not match:
        return None

    tool_name = match.group(1)
    args_part = match.group(2)

    # Strip trailing incomplete key-value pairs (e.g., `, "max` → ``)
    # Find the last complete value: a quoted string, number, boolean, null, or closed ]/}
    # Then truncate at that point
    cleaned = _strip_trailing_incomplete(args_part)

    # Count open/close braces
    open_braces = cleaned.count("{") - cleaned.count("}")
    # Count open/close brackets
    open_brackets = cleaned.count("[") - cleaned.count("]")
    # Count unclosed strings (odd number of quotes)
    in_string = False
    escaped = False
    for ch in cleaned:
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string

    # Build closing sequence
    closing = ""
    if in_string:
        closing += '"'
    closing += "]" * max(0, open_brackets)
    closing += "}" * max(0, open_braces)

    # Also close the outer object if needed
    full_json = '{"tool": "' + tool_name + '", "args": ' + cleaned + closing + "}"
    missing = full_json.count("{") - full_json.count("}")
    if missing > 0:
        full_json += "}" * missing

    try:
        return json.loads(full_json)
    except json.JSONDecodeError:
        return None


def _strip_trailing_incomplete(json_fragment: str) -> str:
    """Strip trailing incomplete JSON tokens from a fragment.

    Keeps everything up to the last complete value (string, number, boolean,
    null, or properly closed ] or }).
    """
    import re

    # Remove trailing comma + any incomplete fragment
    # This handles: , "key, , "key": , , "key": "partial, , "key": 123, etc.
    # Strategy: repeatedly trim from the end until we have valid JSON

    # First: strip trailing comma + incomplete quoted string (unclosed quote)
    cleaned = re.sub(r',\s*"[^"]*$', '', json_fragment)
    # Strip trailing comma + incomplete number or keyword
    cleaned = re.sub(r',\s*[a-zA-Z0-9_.\-]*$', '', cleaned)
    # Strip trailing comma alone
    cleaned = re.sub(r',\s*$', '', cleaned)

    return cleaned


def _fuzzy_match_tool_name(requested: str, available: list[str]) -> str | None:
    """Fuzzy-match a tool name against available tools.

    Handles common LLM mistakes:
    - Extra whitespace: ``"read_file "`` → ``"read_file"``
    - Hyphens vs underscores: ``"list-directory"`` → ``"list_directory"``
    - Case differences: ``"Read_File"`` → ``"read_file"``
    - Prefix/suffix noise: ``"the read_file tool"`` → ``"read_file"``

    Returns the matched name or None.
    """
    cleaned = requested.strip().lower().replace("-", "_")

    if not cleaned:
        return None

    # Exact match after normalization
    if cleaned in available:
        return cleaned

    # Substring match (e.g., "read" matches "read_file")
    matches = [t for t in available if cleaned in t or t in cleaned]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Prefer exact prefix match, then shortest name
        prefix = [t for t in matches if t.startswith(cleaned)]
        if prefix:
            return min(prefix, key=len)
        return min(matches, key=len)

    # Prefix match
    prefix_matches = [t for t in available if t.startswith(cleaned)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    # Try extracting a tool-like word from the string
    import re
    words = re.findall(r"[a-z_][a-z0-9_]*", cleaned)
    for word in words:
        if word in available:
            return word
        sub_matches = [t for t in available if word in t or t in word]
        if len(sub_matches) == 1:
            return sub_matches[0]

    return None


def _coerce_tool_args(
    tool_name: str,
    args: dict[str, Any],
    tool_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Coerce tool arguments to match expected types.

    Handles common LLM mistakes:
    - String numbers → int/float: ``"50"`` → ``50``
    - Missing required args: fills with defaults
    - Extra unknown args: stripped
    """
    if not tool_schema or "properties" not in tool_schema:
        return args

    props = tool_schema["properties"]
    coerced: dict[str, Any] = {}

    for key, schema in props.items():
        if key in args:
            val = args[key]
            expected_type = schema.get("type", "string")

            if expected_type == "integer" and isinstance(val, str):
                try:
                    coerced[key] = int(val)
                except ValueError:
                    coerced[key] = val  # keep original, let it fail downstream
            elif expected_type == "number" and isinstance(val, str):
                try:
                    coerced[key] = float(val)
                except ValueError:
                    coerced[key] = val
            elif expected_type == "boolean" and isinstance(val, str):
                coerced[key] = val.lower() in ("true", "1", "yes")
            else:
                coerced[key] = val

    # Copy any args not in schema (allow extra args)
    for key, val in args.items():
        if key not in coerced:
            coerced[key] = val

    return coerced


def _get_llm_max_retries() -> int:
    """Read LLM max retries from settings, with safe fallback."""
    try:
        from app.config import settings
        return max(0, min(settings.llm_max_retries, 5))
    except Exception as e:
        logger = logging.getLogger("eva.chat_agent")
        logger.debug("Cannot read llm_max_retries from settings: %s", e)
        return 2
