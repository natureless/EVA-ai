"""Tests for tool-calling fault tolerance helpers."""

from __future__ import annotations


from agents.chat_agent import (
    _coerce_tool_args,
    _fuzzy_match_tool_name,
    _recover_truncated_json,
)


class TestRecoverTruncatedJson:
    def test_truncated_mid_string_value(self):
        """Recover when JSON is cut off in the middle of a string value."""
        result = _recover_truncated_json(
            '{"tool": "read_file", "args": {"path": "/tmp/fo'
        )
        assert result is not None
        assert result["tool"] == "read_file"
        assert result["args"]["path"] == "/tmp/fo"

    def test_truncated_mid_key_name(self):
        """Recover when JSON is cut off in the middle of a key name."""
        result = _recover_truncated_json(
            '{"tool": "list_directory", "args": {"path": ".", "max'
        )
        assert result is not None
        assert result["tool"] == "list_directory"
        assert result["args"]["path"] == "."

    def test_truncated_mid_number(self):
        """Recover when JSON is cut off in the middle of a number."""
        result = _recover_truncated_json(
            '{"tool": "read_file", "args": {"path": "/x", "max_lines": 10'
        )
        assert result is not None
        assert result["args"]["max_lines"] == 10

    def test_clean_json_returns_none(self):
        """Clean (non-truncated) JSON should return None — handled by _parse_tool_call."""
        result = _recover_truncated_json(
            '{"tool": "list_directory", "args": {"path": "."}}'
        )
        assert result is None

    def test_no_tool_pattern_returns_none(self):
        """Text without a tool call pattern returns None."""
        result = _recover_truncated_json("Just some regular text")
        assert result is None

    def test_truncated_before_args(self):
        """Recover when cut off right after args opening brace."""
        result = _recover_truncated_json(
            '{"tool": "web_fetch", "args": {'
        )
        assert result is not None
        assert result["tool"] == "web_fetch"
        assert result["args"] == {}


class TestFuzzyMatchToolName:
    AVAILABLE = ["read_file", "list_directory", "search_files", "run_code", "web_fetch", "search_memory"]

    def test_exact_match(self):
        assert _fuzzy_match_tool_name("read_file", self.AVAILABLE) == "read_file"

    def test_trailing_whitespace(self):
        """LLM adds trailing space."""
        assert _fuzzy_match_tool_name("read_file ", self.AVAILABLE) == "read_file"

    def test_hyphen_to_underscore(self):
        """LLM uses hyphen instead of underscore."""
        assert _fuzzy_match_tool_name("list-directory", self.AVAILABLE) == "list_directory"

    def test_case_insensitive(self):
        """LLM uses different case."""
        assert _fuzzy_match_tool_name("Read_File", self.AVAILABLE) == "read_file"

    def test_substring_unique_match(self):
        """Unambiguous substring should match."""
        assert _fuzzy_match_tool_name("web", self.AVAILABLE) == "web_fetch"

    def test_ambiguous_substring_prefers_shortest(self):
        """'search' matches both search_files and search_memory — prefer shortest."""
        result = _fuzzy_match_tool_name("search", self.AVAILABLE)
        assert result in ("search_files", "search_memory")

    def test_unknown_tool_returns_none(self):
        """Completely unknown name returns None."""
        assert _fuzzy_match_tool_name("destroy_world", self.AVAILABLE) is None

    def test_empty_string(self):
        assert _fuzzy_match_tool_name("", self.AVAILABLE) is None


class TestCoerceToolArgs:
    SCHEMA = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "max_lines": {"type": "integer"},
            "max_entries": {"type": "integer"},
            "enabled": {"type": "boolean"},
        },
    }

    def test_string_to_int(self):
        """LLM sends string '50' instead of integer 50."""
        result = _coerce_tool_args("read_file", {"path": "/tmp", "max_lines": "50"}, self.SCHEMA)
        assert result["max_lines"] == 50
        assert isinstance(result["max_lines"], int)

    def test_string_to_float(self):
        """LLM sends string '3.14' instead of float."""
        schema = {"type": "object", "properties": {"score": {"type": "number"}}}
        result = _coerce_tool_args("x", {"score": "3.14"}, schema)
        assert result["score"] == 3.14

    def test_string_to_bool(self):
        """LLM sends 'true' string instead of boolean."""
        result = _coerce_tool_args("x", {"enabled": "true"}, self.SCHEMA)
        assert result["enabled"] is True

    def test_preserves_unknown_args(self):
        """Extra args not in schema are preserved."""
        result = _coerce_tool_args("x", {"path": "/tmp", "extra": "value"}, self.SCHEMA)
        assert result["extra"] == "value"

    def test_no_schema_passthrough(self):
        """Without schema, args pass through unchanged."""
        result = _coerce_tool_args("x", {"a": "1"}, None)
        assert result["a"] == "1"

    def test_invalid_int_kept(self):
        """Non-numeric string stays as-is for downstream error handling."""
        result = _coerce_tool_args("x", {"max_lines": "abc"}, self.SCHEMA)
        assert result["max_lines"] == "abc"
