"""EVA-MVSC 消融实验框架。

所有关键机制必须支持配置关闭，以验证其独立的因果作用。

核心原则:
- 每个机制有独立的 feature flag
- 关闭某个机制后，系统仍应能运行（可能降级）
- 消融结果必须可量化比较
- 不能从"系统还能运行"推出"该机制不必要"

第一版关键实验:
> 在保持基础输入识别能力相近的条件下，分别关闭递归内容、
> 全局广播和自模型，验证内容稳定、灵活访问和主体归属
> 是否发生选择性分离。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ═══════════════════════════════════════════════════════════════
# 消融配置
# ═══════════════════════════════════════════════════════════════

@dataclass
class AblationConfig:
    """可消融的特性配置。

    每个特性默认为 True（开启）。消融实验时关闭特定特性。
    """

    recurrent_content: bool = True       # 递归内容迭代稳定
    global_workspace: bool = True        # 全局工作空间广播
    self_model: bool = True              # 自我模型（归属+能力+叙事）
    value_model: bool = True             # 价值调制（目标+风险+效价）
    episodic_memory: bool = True         # 情景记忆
    narrative_identity: bool = True      # 叙事身份连续性
    metacognition: bool = True           # 元认知（置信度+反思）
    attention_competition: bool = True   # 注意力竞争
    verification: bool = True            # 行动验证
    body_monitoring: bool = True         # 身体监控

    def disable(self, *features: str) -> "AblationConfig":
        """创建关闭指定特性的新配置。"""
        new = AblationConfig(**self.__dict__)
        for f in features:
            if hasattr(new, f):
                setattr(new, f, False)
        return new

    def enable_only(self, *features: str) -> "AblationConfig":
        """创建仅开启指定特性的新配置。"""
        new = AblationConfig(
            recurrent_content=False,
            global_workspace=False,
            self_model=False,
            value_model=False,
            episodic_memory=False,
            narrative_identity=False,
            metacognition=False,
            attention_competition=False,
            verification=False,
            body_monitoring=False,
        )
        for f in features:
            if hasattr(new, f):
                setattr(new, f, True)
        return new

    def to_dict(self) -> dict[str, bool]:
        return self.__dict__.copy()


# ═══════════════════════════════════════════════════════════════
# 消融指标
# ═══════════════════════════════════════════════════════════════

@dataclass
class AblationMetrics:
    """消融实验的测量指标。

    不能压缩成一个"意识总分"。
    各维度必须独立测量和报告。
    """

    # L: 系统可用水平
    uptime_sec: float = 0.0
    error_rate: float = 0.0
    response_latency_ms: float = 0.0

    # K: 内容形成和稳定程度
    content_stability: float = 0.0       # 内容在迭代中的稳定性
    content_diversity: float = 0.0       # 生成内容的多样性
    content_coherence: float = 0.0       # 内容连贯性

    # A: 跨模块因果可用性
    broadcast_success_rate: float = 0.0  # 广播成功影响其他模块的比例
    cross_module_calls: int = 0          # 跨模块调用次数
    isolated_modules: int = 0            # 被隔离的模块数

    # S: 自我归属
    self_attribution_accuracy: float = 0.0  # 正确归因的比例
    external_attribution_accuracy: float = 0.0
    attribution_errors: int = 0

    # V: 价值调制
    risk_response_rate: float = 0.0      # 风险被正确响应的比例
    goal_completion_rate: float = 0.0    # 目标完成率
    priority_consistency: float = 0.0    # 优先级一致性

    # T: 时间连续性
    memory_retrieval_accuracy: float = 0.0
    narrative_consistency: float = 0.0
    task_continuity: float = 0.0         # 跨会话任务连续性

    # Γ: 主体边界
    boundary_violations: int = 0
    internal_causal_closure: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# ═══════════════════════════════════════════════════════════════
# 消融实验运行器
# ═══════════════════════════════════════════════════════════════

@dataclass
class AblationRun:
    """一次消融实验的运行记录。"""

    run_id: str
    config: AblationConfig
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime | None = None
    metrics: AblationMetrics = field(default_factory=AblationMetrics)
    events_processed: int = 0
    notes: str = ""


class AblationRunner:
    """消融实验运行器。

    用法:
        runner = AblationRunner(baseline_config=AblationConfig())
        runner.add_experiment("no_workspace", baseline.disable("global_workspace"))
        runner.add_experiment("no_self_model", baseline.disable("self_model"))
        results = await runner.run_all(test_scenario)
        report = runner.generate_report(results)
    """

    # 预定义消融实验
    PRESET_ABLATIONS = {
        "baseline": "所有特性开启",
        "no_recurrent": "关闭递归内容迭代",
        "no_broadcast": "关闭全局工作空间广播",
        "no_self_model": "关闭自我模型（归属+能力+叙事）",
        "no_value": "所有内容使用相同优先级",
        "no_working_memory": "工作记忆窗口极短",
        "no_narrative": "禁止跨情景整合",
        "no_metacognition": "关闭元认知",
        "no_verification": "关闭行动验证",
        "resource_limited": "限制CPU/并发/推理预算",
        "minimal": "仅保留基础感知和行动",
    }

    def __init__(self, baseline_config: AblationConfig | None = None) -> None:
        self.baseline = baseline_config or AblationConfig()
        self.experiments: dict[str, AblationConfig] = {}
        self.results: list[AblationRun] = []

    def add_experiment(self, name: str, config: AblationConfig) -> None:
        """添加消融实验配置。"""
        self.experiments[name] = config

    def add_preset(self, preset_name: str) -> None:
        """添加预定义消融实验。"""
        presets = {
            "no_recurrent": self.baseline.disable("recurrent_content"),
            "no_broadcast": self.baseline.disable("global_workspace"),
            "no_self_model": self.baseline.disable("self_model", "narrative_identity"),
            "no_value": self.baseline.disable("value_model"),
            "no_working_memory": self.baseline.disable("episodic_memory"),
            "no_narrative": self.baseline.disable("narrative_identity"),
            "no_metacognition": self.baseline.disable("metacognition"),
            "no_verification": self.baseline.disable("verification"),
            "resource_limited": self.baseline.disable("body_monitoring"),
            "minimal": self.baseline.enable_only("recurrent_content"),
        }
        if preset_name in presets:
            self.experiments[preset_name] = presets[preset_name]

    def generate_report(self) -> str:
        """生成消融实验报告。"""
        if not self.results:
            return "No results yet."

        lines = ["# EVA-MVSC Ablation Report", ""]
        baseline_metrics = None

        for run in self.results:
            if run.config == self.baseline:
                baseline_metrics = run.metrics
                break

        for run in self.results:
            name = run.run_id
            desc = self.PRESET_ABLATIONS.get(name, name)
            m = run.metrics

            lines.append(f"## {name}: {desc}")
            lines.append(f"- Events: {run.events_processed}")
            lines.append(f"- Error Rate: {m.error_rate:.3f}")
            lines.append(f"- Content Stability: {m.content_stability:.3f}")
            lines.append(f"- Broadcast Success: {m.broadcast_success_rate:.3f}")
            lines.append(f"- Self Attribution: {m.self_attribution_accuracy:.3f}")
            lines.append(f"- Boundary Violations: {m.boundary_violations}")
            lines.append(f"- Narrative Consistency: {m.narrative_consistency:.3f}")

            # 与基线比较
            if baseline_metrics and run.config != self.baseline:
                lines.append("")
                lines.append("### vs Baseline")
                for attr in ["content_stability", "broadcast_success_rate",
                            "self_attribution_accuracy", "narrative_consistency"]:
                    base_val = getattr(baseline_metrics, attr, 0)
                    run_val = getattr(m, attr, 0)
                    delta = run_val - base_val
                    direction = "↓" if delta < 0 else "↑" if delta > 0 else "→"
                    lines.append(f"- {attr}: {run_val:.3f} ({direction}{abs(delta):.3f})")

            lines.append("")

        return "\n".join(lines)
