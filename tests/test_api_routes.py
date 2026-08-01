"""Tests for Memory Explorer API routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestMemoryExplorerRoutes:
    """Integration tests for /api/memory/entries, /api/memory/search, /api/memory/entry."""

    @pytest.fixture(autouse=True)
    def setup(self, client: TestClient):
        self.client = client

    # ── Tier entries ──────────────────────────────────────

    def test_entries_s1_returns_data(self):
        """S1 (session) entries are browsable."""
        r = self.client.get("/api/memory/entries/S1?limit=5")
        assert r.status_code == 200
        data = r.json()
        assert data["tier"] == "S1"
        assert "entries" in data
        assert "total" in data
        assert isinstance(data["entries"], list)

    def test_entries_s2_returns_data(self):
        """S2 (working) entries are browsable."""
        r = self.client.get("/api/memory/entries/S2?limit=5")
        assert r.status_code == 200
        data = r.json()
        assert data["tier"] == "S2"
        assert isinstance(data["entries"], list)

    def test_entries_s3_returns_data(self):
        """S3 (long-term) entries are browsable."""
        r = self.client.get("/api/memory/entries/S3?limit=5")
        assert r.status_code == 200
        data = r.json()
        assert data["tier"] == "S3"
        assert isinstance(data["entries"], list)

    def test_entries_s4_returns_data(self):
        """S4 (world model) entries are browsable."""
        r = self.client.get("/api/memory/entries/S4?limit=5")
        assert r.status_code == 200
        data = r.json()
        assert data["tier"] == "S4"
        assert isinstance(data["entries"], list)

    def test_entries_s5_returns_data(self):
        """S5 (events) entries are browsable."""
        r = self.client.get("/api/memory/entries/S5?limit=5")
        assert r.status_code == 200
        data = r.json()
        assert data["tier"] == "S5"
        assert isinstance(data["entries"], list)

    def test_entries_unknown_tier(self):
        """Unknown tier returns empty list."""
        r = self.client.get("/api/memory/entries/UNKNOWN?limit=5")
        assert r.status_code == 200
        data = r.json()
        assert data["entries"] == []
        assert data["total"] == 0

    def test_entries_respects_limit(self):
        """Limit parameter is honored."""
        r = self.client.get("/api/memory/entries/S1?limit=3")
        assert r.status_code == 200
        data = r.json()
        assert len(data["entries"]) <= 3

    def test_entries_respects_offset(self):
        """Offset parameter is honored."""
        r = self.client.get("/api/memory/entries/S5?limit=5&offset=0")
        assert r.status_code == 200
        data1 = r.json()

        r = self.client.get("/api/memory/entries/S5?limit=5&offset=3")
        assert r.status_code == 200
        data2 = r.json()

        # With offset, entries should differ (if there are enough)
        if data1["total"] > 3:
            assert data1["entries"] != data2["entries"]

    def test_entries_invalid_limit_clamped(self):
        """Limit > 200 should be rejected with 422."""
        r = self.client.get("/api/memory/entries/S1?limit=999")
        assert r.status_code == 422

    # ── Search ────────────────────────────────────────────

    def test_search_requires_query(self):
        """Empty query should be rejected."""
        r = self.client.post("/api/memory/search", json={"query": "", "tiers": ["S1"]})
        assert r.status_code == 422

    def test_search_across_tiers(self):
        """Search returns results across specified tiers."""
        r = self.client.post(
            "/api/memory/search",
            json={"query": "test", "tiers": ["S2", "S3"], "limit": 5},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["query"] == "test"
        assert "results" in data
        assert "total" in data
        assert isinstance(data["results"], dict)

    def test_search_default_tiers(self):
        """Search defaults to S1, S2, S3 tiers."""
        r = self.client.post(
            "/api/memory/search",
            json={"query": "a"},
        )
        assert r.status_code == 200
        data = r.json()
        # Only S1, S2, S3 keys should be present
        for tier in data["results"]:
            assert tier in ("S1", "S2", "S3")

    def test_search_limit_enforced(self):
        """Search respects the limit parameter."""
        r = self.client.post(
            "/api/memory/search",
            json={"query": "e", "tiers": ["S2"], "limit": 2},
        )
        assert r.status_code == 200
        data = r.json()
        for entries in data["results"].values():
            assert len(entries) <= 2

    def test_search_empty_tiers(self):
        """Empty tiers list returns empty results."""
        r = self.client.post(
            "/api/memory/search",
            json={"query": "test", "tiers": [], "limit": 5},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["results"] == {}

    # ── Single entry detail ───────────────────────────────

    def test_entry_not_found(self):
        """Non-existent entry returns not found."""
        r = self.client.get("/api/memory/entry/S2/nonexistent-id-12345")
        assert r.status_code == 200
        data = r.json()
        assert data["found"] is False
        assert data["tier"] == "S2"

    def test_entry_unknown_tier(self):
        """Unknown tier returns not found."""
        r = self.client.get("/api/memory/entry/UNKNOWN/some-id")
        assert r.status_code == 200
        data = r.json()
        assert data["found"] is False


class TestMemoryTiersRoute:
    """Tests for the /api/memory/tiers endpoint (in routes_memory.py)."""

    @pytest.fixture(autouse=True)
    def setup(self, client: TestClient):
        self.client = client

    def test_tiers_raw_format(self):
        """Default format returns raw stats keys."""
        r = self.client.get("/api/memory/tiers")
        assert r.status_code == 200
        data = r.json()
        assert "S1_session" in data
        assert "S2_working" in data
        assert "S3_long_term" in data
        assert "S4_world_model" in data
        assert "S5_event_trace" in data

    def test_tiers_explorer_format(self):
        """Explorer format returns transformed tiers list."""
        r = self.client.get("/api/memory/tiers?format=explorer")
        assert r.status_code == 200
        data = r.json()
        assert "tiers" in data
        assert "total_entries" in data
        assert len(data["tiers"]) == 5
        for t in data["tiers"]:
            assert "name" in t
            assert "label" in t
            assert "entries" in t
            assert "ttl" in t
            assert "storage" in t


class TestToolsRoutes:
    """Tests for /api/tools endpoints."""

    @pytest.fixture(autouse=True)
    def setup(self, client: TestClient):
        self.client = client

    def test_list_tools(self):
        """List all available tools."""
        r = self.client.get("/api/tools")
        assert r.status_code == 200
        data = r.json()
        assert "tools" in data
        assert "count" in data
        assert data["count"] >= 5  # at least the 5 built-in tools
        for tool in data["tools"]:
            assert "name" in tool
            assert "description" in tool
            assert "parameters" in tool

    def test_call_tool_success(self):
        """Call a tool directly and get a result."""
        r = self.client.post(
            "/api/tools/call",
            json={"tool": "list_directory", "args": {"path": ".", "max_entries": 3}},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["tool"] == "list_directory"
        assert "result" in data
        assert "formatted" in data

    def test_call_tool_unknown(self):
        """Calling an unknown tool returns error."""
        r = self.client.post(
            "/api/tools/call",
            json={"tool": "nonexistent_tool", "args": {}},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert "unknown tool" in data["result"]["error"]

    def test_call_tool_invalid_args_type(self):
        """Non-dict args should be rejected."""
        r = self.client.post(
            "/api/tools/call",
            json={"tool": "read_file", "args": "not a dict"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
