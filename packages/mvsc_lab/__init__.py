"""packages/mvsc_lab 包 — MVSC研究层。"""

from packages.mvsc_lab.ablations import (
    AblationConfig,
    AblationMetrics,
    AblationRun,
    AblationRunner,
)
from packages.mvsc_lab.integration import (
    AblationMetricsCollector,
    inject_ablation_to_bootstrap,
    load_ablation_config,
    toggle_feature,
)

__all__ = [
    "AblationConfig",
    "AblationMetrics",
    "AblationRun",
    "AblationRunner",
    "AblationMetricsCollector",
    "inject_ablation_to_bootstrap",
    "load_ablation_config",
    "toggle_feature",
]
