"""AblationIntegration — 将 MVSC 消融框架连接到现有 EVA bootstrap。

提供:
1. 从 configs/experiments.yaml 加载特性开关
2. 在 bootstrap 中注入 feature_flags 到 CognitionLoop
3. 消融指标收集端点
4. 运行时特性开关切换 (通过 admin API)

与现有代码的关系:
- 不改动 bootstrap 核心流程
- 通过环境变量 EVA_ABLATION_PRESET 选择预设
- 消融指标通过现有的 /api/state 端点暴露
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from packages.mvsc_lab.ablations import AblationConfig, AblationMetrics

logger = logging.getLogger("eva.mvsc.ablation_integration")


# ═══════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════

def load_ablation_config(
    config_path: str | Path = "configs/experiments.yaml",
    preset: str | None = None,
) -> AblationConfig:
    """从 YAML 配置文件加载消融配置。

    优先级:
    1. 函数参数 preset
    2. 环境变量 EVA_ABLATION_PRESET
    3. 默认: 所有特性开启 (baseline)

    Args:
        config_path: experiments.yaml 路径
        preset: 预设名称 (baseline, no_broadcast, minimal, ...)

    Returns:
        AblationConfig 实例
    """
    import os

    cfg = AblationConfig()  # 默认全开

    # 确定使用的预设
    if preset is None:
        preset = os.environ.get("EVA_ABLATION_PRESET", "baseline")

    # 加载 YAML
    path = Path(config_path)
    if not path.exists():
        logger.warning("experiments.yaml not found at %s — using defaults", path)
        return cfg

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        logger.exception("failed to load experiments.yaml")
        return cfg

    # 应用预设
    ablations = data.get("ablations", {})
    if preset in ablations:
        preset_data = ablations[preset]
        features = preset_data.get("features", {})
        for key, value in features.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        logger.info(
            "ablation preset '%s' loaded: %s — %s",
            preset,
            preset_data.get("description", ""),
            {k: v for k, v in features.items() if not v},
        )
    elif preset != "baseline":
        logger.warning("unknown ablation preset '%s' — using baseline", preset)

    return cfg


# ═══════════════════════════════════════════════════════════════
# 指标收集器
# ═══════════════════════════════════════════════════════════════

class AblationMetricsCollector:
    """在运行时收集消融指标。

    连接到现有的 CognitionLoop 和系统状态，
    收集 L/K/A/S/V/T/Γ 各维度指标。
    """

    def __init__(self) -> None:
        self._metrics = AblationMetrics()
        self._tick_count = 0
        self._error_count = 0
        self._broadcast_count = 0
        self._broadcast_success_count = 0
        self._attribution_correct = 0
        self._attribution_total = 0

    def record_tick(
        self,
        system_state: dict[str, Any],
        conscious_state: Any = None,
    ) -> None:
        """每个认知循环后记录指标。"""
        self._tick_count += 1

        # L: 系统可用水平
        self._metrics.uptime_sec = self._tick_count * 0.5  # 估算
        if system_state.get("pending_events", 0) > 100:
            self._error_count += 1
        self._metrics.error_rate = (
            self._error_count / self._tick_count if self._tick_count > 0 else 0
        )

        # A: 跨模块可用性
        if conscious_state and hasattr(conscious_state, "workspace"):
            ws = conscious_state.workspace
            self._broadcast_count += 1
            if len(ws) > 0:
                self._broadcast_success_count += 1
            self._metrics.broadcast_success_rate = (
                self._broadcast_success_count / self._broadcast_count
                if self._broadcast_count > 0
                else 0
            )

        # S: 自我归属 (从 system_state 推断)
        if system_state.get("stability_score"):
            self._metrics.self_attribution_accuracy = system_state["stability_score"]

        # T: 时间连续性
        self._metrics.narrative_consistency = system_state.get("stability_score", 1.0)

        # V: 价值调制
        if system_state.get("last_proactive_reason") == "stagnation_detected":
            self._metrics.risk_response_rate = 1.0

    def snapshot(self) -> AblationMetrics:
        """获取当前指标快照。"""
        return self._metrics

    def to_dict(self) -> dict[str, Any]:
        """转为字典（用于 API 响应）。"""
        return self._metrics.to_dict()


# ═══════════════════════════════════════════════════════════════
# Bootstrap 集成
# ═══════════════════════════════════════════════════════════════

def inject_ablation_to_bootstrap(
    container: Any,
    ablation_config: AblationConfig | None = None,
) -> AblationMetricsCollector:
    """在 bootstrap 完成后注入消融配置。

    将 AblationConfig 的 feature_flags 注入到:
    - container.system_state["mvsc_feature_flags"]
    - 后续 AdaptedCognitionLoop 初始化

    Args:
        container: AppContainer 实例
        ablation_config: 消融配置（None = 全开）

    Returns:
        AblationMetricsCollector 实例
    """
    if ablation_config is None:
        ablation_config = load_ablation_config()

    flags = ablation_config.to_dict()

    # 注入到 system_state
    if hasattr(container, "system_state"):
        container.system_state["mvsc_feature_flags"] = flags
        container.system_state["mvsc_ablation_preset"] = (
            ablation_config.to_dict()
        )

    # 注入到 container
    container.ablation_config = ablation_config  # type: ignore[attr-defined]

    collector = AblationMetricsCollector()
    container.ablation_collector = collector  # type: ignore[attr-defined]

    # 记录关闭的特性
    disabled = [k for k, v in flags.items() if not v]
    if disabled:
        logger.info("ablation active — disabled features: %s", disabled)
    else:
        logger.info("ablation: baseline (all features enabled)")

    return collector


# ═══════════════════════════════════════════════════════════════
# 运行时切换 (用于消融实验)
# ═══════════════════════════════════════════════════════════════

def toggle_feature(container: Any, feature: str, enabled: bool) -> bool:
    """运行时切换单个特性。

    用于消融实验中的 A/B 测试。

    Returns:
        True 如果切换成功
    """
    if not hasattr(container, "ablation_config"):
        logger.warning("no ablation_config on container — cannot toggle")
        return False

    cfg: AblationConfig = container.ablation_config

    if not hasattr(cfg, feature):
        logger.warning("unknown feature: %s", feature)
        return False

    setattr(cfg, feature, enabled)

    # 同步到 system_state
    if hasattr(container, "system_state"):
        container.system_state["mvsc_feature_flags"] = cfg.to_dict()

    logger.info("feature '%s' toggled → %s", feature, enabled)
    return True
