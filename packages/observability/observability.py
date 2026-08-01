"""Observability — EVA-MVSC 可观测性层。

包含:
- MetricsCollector 指标收集器
- Tracer 追踪器 (事件链路追踪)
- HealthReporter 健康报告器
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("eva.observability")


# ═══════════════════════════════════════════════════════════════
# MetricsCollector
# ═══════════════════════════════════════════════════════════════

class MetricsCollector:
    """指标收集器 — 收集运行时指标。

    MVSC 测量维度:
    - L: 系统可用水平
    - K: 内容形成和稳定程度
    - A: 跨模块因果可用性
    - S: 自我归属
    - V: 价值调制
    - T: 时间连续性
    - Γ: 主体边界
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._start_time = time.time()

    def increment(self, name: str, value: int = 1) -> None:
        self._counters[name] += value

    def gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        self._histograms[name].append(value)
        if len(self._histograms[name]) > 1000:
            self._histograms[name] = self._histograms[name][-1000:]

    def snapshot(self) -> dict[str, Any]:
        """获取指标快照。"""
        uptime = time.time() - self._start_time

        # 计算直方图统计
        histograms = {}
        for name, values in self._histograms.items():
            if values:
                sorted_vals = sorted(values)
                histograms[name] = {
                    "count": len(values),
                    "min": sorted_vals[0],
                    "max": sorted_vals[-1],
                    "avg": sum(values) / len(values),
                    "p50": sorted_vals[len(values) // 2],
                    "p95": sorted_vals[int(len(values) * 0.95)],
                }

        return {
            "uptime_sec": round(uptime, 1),
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": histograms,
        }


# ═══════════════════════════════════════════════════════════════
# Tracer
# ═══════════════════════════════════════════════════════════════

class Tracer:
    """追踪器 — 事件链路追踪。

    追踪 causation_id 链，构建完整的因果图。
    """

    def __init__(self, max_spans: int = 500) -> None:
        self._spans: dict[str, dict[str, Any]] = {}
        self._max_spans = max_spans

    def start_span(
        self,
        span_id: str,
        parent_id: str | None = None,
        operation: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """开始一个追踪 span。"""
        if len(self._spans) >= self._max_spans:
            oldest = min(self._spans.keys(), key=lambda k: self._spans[k].get("start_time", 0))
            del self._spans[oldest]

        self._spans[span_id] = {
            "span_id": span_id,
            "parent_id": parent_id,
            "operation": operation,
            "start_time": time.time(),
            "end_time": None,
            "duration_ms": None,
            "metadata": metadata or {},
            "status": "running",
        }

    def end_span(self, span_id: str, status: str = "ok") -> None:
        """结束一个追踪 span。"""
        span = self._spans.get(span_id)
        if span is None:
            return
        span["end_time"] = time.time()
        span["duration_ms"] = round((span["end_time"] - span["start_time"]) * 1000, 2)
        span["status"] = status

    def get_trace(self, span_id: str) -> dict[str, Any] | None:
        """获取单个 span。"""
        return self._spans.get(span_id)

    def get_trace_tree(self, root_id: str) -> list[dict[str, Any]]:
        """获取以 root_id 为根的追踪树。"""
        tree: list[dict[str, Any]] = []
        root = self._spans.get(root_id)
        if root:
            tree.append(root)
        for span in self._spans.values():
            if span.get("parent_id") == root_id:
                tree.append(span)
        return sorted(tree, key=lambda s: s.get("start_time", 0))

    @property
    def stats(self) -> dict[str, Any]:
        running = sum(1 for s in self._spans.values() if s["status"] == "running")
        completed = sum(1 for s in self._spans.values() if s["status"] != "running")
        durations = [s["duration_ms"] for s in self._spans.values() if s["duration_ms"] is not None]
        return {
            "total_spans": len(self._spans),
            "running": running,
            "completed": completed,
            "avg_duration_ms": round(sum(durations) / len(durations), 2) if durations else 0,
        }


# ═══════════════════════════════════════════════════════════════
# HealthReporter
# ═══════════════════════════════════════════════════════════════

class HealthReporter:
    """健康报告器 — 聚合系统健康状态。"""

    def __init__(self) -> None:
        self._checks: dict[str, dict[str, Any]] = {}

    def report(self, name: str, healthy: bool, detail: str = "") -> None:
        self._checks[name] = {
            "healthy": healthy,
            "detail": detail,
            "last_checked": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def overall_healthy(self) -> bool:
        if not self._checks:
            return True
        return all(c["healthy"] for c in self._checks.values())

    def snapshot(self) -> dict[str, Any]:
        return {
            "overall": "healthy" if self.overall_healthy else "degraded",
            "checks": dict(self._checks),
            "check_count": len(self._checks),
            "failed_checks": sum(1 for c in self._checks.values() if not c["healthy"]),
        }
