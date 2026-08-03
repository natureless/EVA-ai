"""Integration tests for Phase 5-6: ExecutorToolAdapter + AblationIntegration.

Phase 5: ExecutorToolAdapter wrapping existing Executors.
Phase 6: Ablation config loading + bootstrap injection + runtime toggle.
"""

import os
import sys

import pytest

sys.path.insert(0, ".")

from packages.contracts.protocols import ToolRequest, ToolResult
from packages.tools.executor_adapter import (
    ExecutorToolAdapter,
    ToolRegistryAdapter,
    create_executor_adapters,
)
from packages.mvsc_lab.ablations import AblationConfig
from packages.mvsc_lab.integration import (
    AblationMetricsCollector,
    inject_ablation_to_bootstrap,
    load_ablation_config,
    toggle_feature,
)


# ═══════════════════════════════════════════════════════════
# Phase 5: ExecutorToolAdapter tests
# ═══════════════════════════════════════════════════════════

class FakeExecutor:
    """Fake executor that returns predictable results."""

    name = "fake"

    def execute(self, action, params, task_id="", token_id="", token_manager=None):
        return {
            "ok": True,
            "summary": f"Executed {action}",
            "status": "success",
            "files_affected": [params.get("path", "")] if params.get("path") else [],
        }


class FailingExecutor:
    """Fake executor that always fails."""

    name = "failing"

    def execute(self, action, params, task_id="", token_id="", token_manager=None):
        raise RuntimeError("executor failure")


class TestExecutorToolAdapter:
    """Test ExecutorToolAdapter wrapping existing Executors."""

    @pytest.fixture
    def adapter(self):
        return ExecutorToolAdapter("fake", FakeExecutor())

    @pytest.fixture
    def failing_adapter(self):
        return ExecutorToolAdapter("failing", FailingExecutor())

    @pytest.mark.asyncio
    async def test_validate_passes_for_matching_tool_id(self, adapter):
        """Validation should pass when tool_id matches."""
        req = ToolRequest(tool_id="fake", parameters={"action": "read"})
        await adapter.validate(req)  # should not raise

    @pytest.mark.asyncio
    async def test_validate_rejects_mismatched_tool_id(self, adapter):
        """Validation should fail when tool_id doesn't match."""
        req = ToolRequest(tool_id="wrong_tool", parameters={"action": "read"})
        with pytest.raises(ValueError, match="Tool ID mismatch"):
            await adapter.validate(req)

    @pytest.mark.asyncio
    async def test_validate_rejects_empty_parameters(self, adapter):
        """Validation should fail for empty parameters."""
        req = ToolRequest(tool_id="fake", parameters={})
        with pytest.raises(ValueError, match="cannot be empty"):
            await adapter.validate(req)

    @pytest.mark.asyncio
    async def test_execute_returns_tool_result(self, adapter):
        """Execute should return ToolResult with correct fields."""
        req = ToolRequest(
            tool_id="fake",
            parameters={"action": "read", "path": "/test/file.txt"},
        )
        result = await adapter.execute(req)

        assert isinstance(result, ToolResult)
        assert result.ok is True
        assert result.error is None
        assert result.duration_ms >= 0
        assert "fake:read" in result.side_effects
        assert "/test/file.txt" in result.side_effects

    @pytest.mark.asyncio
    async def test_execute_handles_errors(self, failing_adapter):
        """Execute should catch executor errors and return failed ToolResult."""
        req = ToolRequest(
            tool_id="failing",
            parameters={"action": "crash"},
        )
        result = await failing_adapter.execute(req)

        assert result.ok is False
        assert result.error is not None
        assert "executor failure" in result.error

    @pytest.mark.asyncio
    async def test_create_executor_adapters(self):
        """Factory should create adapters for all executors."""
        executors = {
            "file": FakeExecutor(),
            "code": FakeExecutor(),
            "browser": FakeExecutor(),
            "api": FakeExecutor(),
            "comms": FakeExecutor(),
            "playwright_browser": FakeExecutor(),  # should be skipped
        }
        adapters = create_executor_adapters(executors)

        assert len(adapters) == 5  # playwright skipped
        assert "file" in adapters
        assert "code" in adapters
        assert "playwright_browser" not in adapters


class TestToolRegistryAdapter:
    """Test ToolRegistryAdapter."""

    def test_list_tools_empty(self):
        """Empty registry should return empty list."""
        adapter = ToolRegistryAdapter()
        assert adapter.list_tools() == []
        assert adapter.tool_names() == []

    def test_get_tool_returns_none_for_unknown(self):
        """Unknown tool should return None."""
        adapter = ToolRegistryAdapter()
        assert adapter.get_tool("nonexistent") is None


# ═══════════════════════════════════════════════════════════
# Phase 6: AblationIntegration tests
# ═══════════════════════════════════════════════════════════

class TestAblationConfigLoading:
    """Test ablation config loading from YAML."""

    def test_load_default_all_enabled(self):
        """Default config should have all features enabled."""
        cfg = load_ablation_config(preset="baseline")
        assert cfg.global_workspace is True
        assert cfg.self_model is True
        assert cfg.recurrent_content is True
        assert cfg.metacognition is True

    def test_load_preset_from_yaml(self):
        """Should load a named preset from experiments.yaml."""
        cfg = load_ablation_config(preset="no_broadcast")
        # no_broadcast disables global_workspace
        assert cfg.global_workspace is False

    def test_load_minimal_preset(self):
        """Minimal preset should disable most features."""
        cfg = load_ablation_config(preset="minimal")
        assert cfg.global_workspace is False
        assert cfg.self_model is False
        assert cfg.metacognition is False

    def test_unknown_preset_falls_back_to_baseline(self):
        """Unknown preset should fall back to baseline (all enabled)."""
        cfg = load_ablation_config(preset="nonexistent_preset")
        assert cfg.global_workspace is True

    def test_env_var_override(self):
        """EVA_ABLATION_PRESET env var should override default."""
        os.environ["EVA_ABLATION_PRESET"] = "no_self_model"
        try:
            cfg = load_ablation_config()
            assert cfg.self_model is False
        finally:
            del os.environ["EVA_ABLATION_PRESET"]

    def test_disable_creates_correct_config(self):
        """AblationConfig.disable() should work correctly."""
        cfg = AblationConfig()
        modified = cfg.disable("global_workspace", "metacognition")
        assert modified.global_workspace is False
        assert modified.metacognition is False
        assert modified.self_model is True  # unchanged

    def test_enable_only(self):
        """AblationConfig.enable_only() should disable everything else."""
        cfg = AblationConfig()
        minimal = cfg.enable_only("recurrent_content")
        assert minimal.recurrent_content is True
        assert minimal.global_workspace is False
        assert minimal.self_model is False
        assert minimal.metacognition is False


class TestAblationMetricsCollector:
    """Test runtime metrics collection."""

    def test_initial_metrics_are_zero(self):
        collector = AblationMetricsCollector()
        m = collector.snapshot()
        assert m.error_rate == 0.0
        assert m.broadcast_success_rate == 0.0

    def test_record_tick_updates_metrics(self):
        collector = AblationMetricsCollector()
        collector.record_tick({"pending_events": 0, "stability_score": 0.9})
        m = collector.snapshot()
        assert m.uptime_sec >= 0
        assert m.self_attribution_accuracy == 0.9

    def test_to_dict_serializable(self):
        collector = AblationMetricsCollector()
        collector.record_tick({"pending_events": 0, "stability_score": 1.0})
        d = collector.to_dict()
        assert isinstance(d, dict)
        assert "error_rate" in d
        assert "broadcast_success_rate" in d


class TestBootstrapInjection:
    """Test ablation injection into bootstrap container."""

    def test_inject_adds_feature_flags_to_system_state(self):
        """inject_ablation_to_bootstrap should add flags to system_state."""

        class FakeContainer:
            def __init__(self):
                self.system_state: dict = {}
                self.ablation_config = None
                self.ablation_collector = None

        container = FakeContainer()
        cfg = AblationConfig()
        cfg = cfg.disable("metacognition")

        collector = inject_ablation_to_bootstrap(container, cfg)

        assert "mvsc_feature_flags" in container.system_state
        assert container.system_state["mvsc_feature_flags"]["metacognition"] is False
        assert container.system_state["mvsc_feature_flags"]["global_workspace"] is True
        assert container.ablation_config is cfg
        assert container.ablation_collector is collector

    def test_toggle_feature_updates_config(self):
        """toggle_feature should update both config and system_state."""

        class FakeContainer:
            system_state: dict = {"mvsc_feature_flags": {}}
            ablation_config = AblationConfig()

        container = FakeContainer()
        assert container.ablation_config.metacognition is True

        result = toggle_feature(container, "metacognition", False)
        assert result is True
        assert container.ablation_config.metacognition is False
        assert container.system_state["mvsc_feature_flags"]["metacognition"] is False

    def test_toggle_unknown_feature_returns_false(self):
        """Toggling unknown feature should return False."""

        class FakeContainer:
            system_state: dict = {}
            ablation_config = AblationConfig()

        result = toggle_feature(FakeContainer(), "nonexistent", True)
        assert result is False
