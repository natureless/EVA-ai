#!/usr/bin/env python3
"""EVA-MVSC Pipeline 基准测试。

测量 MVSC 13 阶段认知循环的端到端性能。
不依赖 LLM — 使用 ContentEngine 的纯规则路径。

Usage:
    python scripts/benchmark_pipeline.py
    python scripts/benchmark_pipeline.py --iterations 1000
    python scripts/benchmark_pipeline.py --ablation no_broadcast
"""

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.contracts.events import EventEnvelope, EventFamily
from packages.cognition.adapted_loop import create_adapted_loop


class FakeContainer:
    """最小化容器用于基准测试。"""
    def __init__(self):
        from event.event_bus import EventBus
        self.event_bus = EventBus()
        self.system_state = {"focus": "idle", "mode": "active", "ready": True}
        self.world_model = FakeWorldModel()
        self.agent_router = FakeRouter()
        self.orchestrator = FakeOrch()
        self.tiered_memory = FakeTM()
        self.self_model = None
        self.self_model_store = None
        self.prediction_tracker = None
        self.memory_governor = None
        self.planner = None
        self.ws_manager = None
        self.loop = None  # needed by _update_body


class FakeWorldModel:
    focus = "idle"
    mode = "active"
    active_tasks = []
    def apply_user_message(self, text): self.focus = text[:80]
    def apply_agent_result(self, **kw): pass


class FakeRouter:
    def route(self, preferred, task): return preferred


class FakeOrch:
    def execute(self, agent, task):
        from agents.base_agent import AgentResult
        return AgentResult(ok=True, agent=agent, content="ok", summary="ok"), 1


class FakeTM:
    def ingest(self, *a, **kw): pass
    def recall(self, *a, **kw): return []
    def maintenance(self): pass


async def run_benchmark(iterations: int, ablation: str | None):
    container = FakeContainer()
    loop = create_adapted_loop(container)

    if ablation:
        from packages.mvsc_lab.integration import load_ablation_config
        cfg = load_ablation_config(preset=ablation)
        loop.feature_flags = cfg.to_dict()

    event = EventEnvelope(
        event_type=EventFamily.PERCEPTION.USER_MESSAGE,
        source="benchmark",
        payload={"text": "benchmark query"},
    )

    # Warmup
    for _ in range(5):
        await loop.run_once(event)

    # Benchmark
    timings: list[float] = []
    phase_totals: dict[str, list[float]] = {}

    for i in range(iterations):
        t0 = time.perf_counter()
        state = await loop.run_once(event)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        timings.append(elapsed_ms)

        for phase, t in loop.phase_timings.items():
            phase_totals.setdefault(phase, []).append(t)

    # Report
    print(f"\n{'='*60}")
    print(f"  EVA-MVSC Pipeline Benchmark")
    print(f"{'='*60}")
    print(f"  Iterations:    {iterations}")
    print(f"  Ablation:      {ablation or 'baseline'}")
    print(f"  Features:      {sum(1 for v in loop.feature_flags.values() if v)}/{len(loop.feature_flags)} enabled")
    print(f"")

    sorted_t = sorted(timings)
    print(f"  Total per tick:")
    print(f"    Mean:   {statistics.mean(timings):.3f} ms")
    print(f"    Median: {statistics.median(timings):.3f} ms")
    print(f"    P50:    {sorted_t[len(sorted_t)//2]:.3f} ms")
    print(f"    P95:    {sorted_t[int(len(sorted_t)*0.95)]:.3f} ms")
    print(f"    P99:    {sorted_t[int(len(sorted_t)*0.99)]:.3f} ms")
    print(f"    Min:    {min(timings):.3f} ms")
    print(f"    Max:    {max(timings):.3f} ms")
    print(f"")

    print(f"  Per phase (mean):")
    for phase in ['perceive', 'update_world', 'update_body', 'generate_content',
                   'select_attention', 'broadcast', 'attribute_self', 'evaluate',
                   'decide', 'plan', 'act', 'verify', 'consolidate']:
        pts = phase_totals.get(phase, [])
        if pts:
            print(f"    {phase:20s}: {statistics.mean(pts):8.3f} ms")
    print(f"{'='*60}\n")

    # Throughput
    total_sec = sum(timings) / 1000
    print(f"  Throughput: {iterations/total_sec:.1f} ticks/sec")
    print(f"  Total time: {total_sec:.2f}s\n")


def main():
    parser = argparse.ArgumentParser(description="EVA-MVSC Pipeline Benchmark")
    parser.add_argument("--iterations", type=int, default=100, help="Number of ticks")
    parser.add_argument("--ablation", type=str, default=None, help="Ablation preset")
    args = parser.parse_args()

    asyncio.run(run_benchmark(args.iterations, args.ablation))


if __name__ == "__main__":
    main()
