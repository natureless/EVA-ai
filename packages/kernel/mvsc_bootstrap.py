"""MVSC Bootstrap Integration — 将 MVSC 组件插入现有 EVA bootstrap 流程。

这是整个 MVSC 融合的最终集成点。通过环境变量 EVA_ENABLE_MVSC_PIPELINE=true
启用后，bootstrap 会:
1. 创建 EventStore + EventBusAdapter 包装现有 EventBus
2. 创建 StateBridge 连接 system_state ↔ ConsciousState
3. 创建 AdaptedCognitionLoop 替代现有 CognitionLoop
4. 注入 AblationConfig + AblationMetricsCollector

所有现有组件保持不变，MVSC 组件通过适配器模式与现有代码共存。

Usage (in app/bootstrap.py):

    if settings.enable_mvsc_pipeline:
        mvsc_components = integrate_mvsc(container_dict, settings)
        container = mvsc_components["container"]
"""

from __future__ import annotations

import logging
from typing import Any

from packages.kernel.event_bus_adapter import EventBusAdapter
from packages.kernel.event_store import EventStore
from packages.kernel.state_bridge import (
    system_state_to_conscious,
)
# Lazy imports to avoid circular dependency with cognition package
# (adapted_loop imports from kernel.state_bridge)
from packages.mvsc_lab.integration import (
    inject_ablation_to_bootstrap,
    load_ablation_config,
)

logger = logging.getLogger("eva.kernel.mvsc_bootstrap")


# ═══════════════════════════════════════════════════════════════
# MVSC Bootstrap 集成
# ═══════════════════════════════════════════════════════════════

def integrate_mvsc(
    container: Any,  # AppContainer
    settings: Any,
    ablation_preset: str | None = None,
) -> dict[str, Any]:
    """将 MVSC 组件集成到已 bootstrap 的 AppContainer 中。

    在现有 bootstrap_system() 的最后阶段调用此函数。
    它会:
    1. 创建 EventStore + EventBusAdapter
    2. 创建 AdaptedCognitionLoop
    3. 注入消融配置

    Args:
        container: 已初始化的 AppContainer
        settings: EVA Settings 实例
        ablation_preset: 消融预设名称 (None = 从环境变量读取)

    Returns:
        {"event_store": ..., "event_adapter": ..., "mvsc_loop": ..., "collector": ...}
    """
    logger.info("MVSC integration start")

    # ── 1. Event Store ───────────────────────────────────────
    event_store = EventStore("data/mvsc_event_store.db")
    logger.info("MVSC event store initialized")

    # ── 2. Event Bus Adapter ─────────────────────────────────
    event_adapter = EventBusAdapter(
        legacy_bus=container.event_bus,
        event_store=event_store,
        subject_id="eva-001",
    )
    logger.info("MVSC event bus adapter initialized")

    # ── 3. State Bridge ──────────────────────────────────────
    # 将现有 system_state 提升为 ConsciousState
    initial_cs = system_state_to_conscious(
        container.system_state,
        subject_id="eva-001",
    )
    logger.info(
        "MVSC state bridge: system_state → ConsciousState (mode=%s)",
        initial_cs.runtime_mode.value,
    )

    # ── 4. Adapted Cognition Loop ────────────────────────────
    from packages.cognition.adapted_loop import create_adapted_loop  # lazy import
    mvsc_loop = create_adapted_loop(container)
    logger.info("MVSC adapted cognition loop created")

    # ── 5. Ablation Integration ──────────────────────────────
    ablation_config = load_ablation_config(preset=ablation_preset)
    collector = inject_ablation_to_bootstrap(container, ablation_config)

    # 将 ablation feature_flags 同步到 MVSC loop
    mvsc_loop.feature_flags = ablation_config.to_dict()

    logger.info(
        "MVSC ablation: %d features enabled, %d disabled",
        sum(1 for v in ablation_config.to_dict().values() if v),
        sum(1 for v in ablation_config.to_dict().values() if not v),
    )

    # ── 6. 组装返回 ──────────────────────────────────────────
    result = {
        "event_store": event_store,
        "event_adapter": event_adapter,
        "mvsc_loop": mvsc_loop,
        "initial_conscious_state": initial_cs,
        "ablation_config": ablation_config,
        "collector": collector,
    }

    # 注入到 container
    container.mvsc_event_store = event_store  # type: ignore[attr-defined]
    container.mvsc_event_adapter = event_adapter  # type: ignore[attr-defined]
    container.mvsc_loop = mvsc_loop  # type: ignore[attr-defined]
    container.mvsc_initial_state = initial_cs  # type: ignore[attr-defined]

    logger.info("MVSC integration complete")
    return result


# ═══════════════════════════════════════════════════════════════
# MVSC Shutdown
# ═══════════════════════════════════════════════════════════════

def shutdown_mvsc(mvsc_components: dict[str, Any]) -> None:
    """安全关闭 MVSC 组件。

    在现有 shutdown_system() 中调用。
    """
    logger.info("MVSC shutdown start")

    # 1. 保存 EventStore
    event_store = mvsc_components.get("event_store")
    if event_store:
        try:
            event_store.close()
            logger.info("MVSC event store closed")
        except Exception:
            logger.exception("MVSC event store close failed")

    # 2. 最终指标快照
    collector = mvsc_components.get("collector")
    if collector:
        try:
            final_metrics = collector.to_dict()
            logger.info("MVSC final metrics: %s", final_metrics)
        except Exception:
            pass

    logger.info("MVSC shutdown complete")


# ═══════════════════════════════════════════════════════════════
# 便捷: 完整 bootstrap 集成片段
# ═══════════════════════════════════════════════════════════════

# 将此代码添加到 bootstrap_system() 函数末尾，
# 在 `return AppContainer(...)` 之前:

BOOTSTRAP_SNIPPET = """
    # ── MVSC integration (feature-flagged) ──
    mvsc_components = None
    if settings.enable_mvsc_pipeline:
        from packages.kernel.mvsc_bootstrap import integrate_mvsc
        mvsc_components = integrate_mvsc(container_dict, settings)

    # ... 构造 AppContainer 时注入 mvsc_components ...
"""
