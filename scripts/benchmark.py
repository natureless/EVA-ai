"""EVA performance benchmark suite.

Measures latency, throughput, and resource usage across all layers:
- Chat API latency (p50/p95/p99/mean)
- Policy engine evaluation speed
- Memory tier operations (S1-S5 read/write)
- World model entity ops
- Executor framework overhead

Usage:
    python scripts/benchmark.py                # quick run (100 requests, 10 concurrent)
    python scripts/benchmark.py --total 500 --concurrency 20 --report
    python scripts/benchmark.py --in-process   # bypass HTTP, direct ASGI
    python scripts/benchmark.py --json         # JSON output for CI
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

# add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx


# ── CLI ─────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="EVA performance benchmark")
    p.add_argument("--total", type=int, default=100, help="total requests (default: 100)")
    p.add_argument("--concurrency", type=int, default=10, help="concurrent workers (default: 10)")
    p.add_argument("--port", type=int, default=8000, help="server port (default: 8000)")
    p.add_argument("--timeout", type=float, default=30.0, help="request timeout seconds")
    p.add_argument("--in-process", action="store_true", help="use ASGI transport (no server needed)")
    p.add_argument("--json", action="store_true", help="output JSON report")
    p.add_argument("--report", action="store_true", help="verbose report with per-layer breakdown")
    p.add_argument("--warmup", type=int, default=5, help="warmup requests (default: 5)")
    return p.parse_args()


# ── Benchmark runner ────────────────────────────────────────

class BenchmarkRunner:
    def __init__(self, args):
        self.args = args
        self.base_url = f"http://127.0.0.1:{args.port}"
        self.results: dict[str, list[float]] = {
            "chat": [],
            "state": [],
            "health": [],
            "memory_tiers": [],
            "policy_state": [],
        }
        self.layer_results: dict[str, dict] = {}

    def _client(self):
        if self.args.in_process:
            from app.main import app
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                timeout=self.args.timeout,
            )
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.args.timeout)

    # ── HTTP benchmarks ─────────────────────────────────────

    async def _make_request(self, client, method: str, path: str, json_body=None):
        start = time.perf_counter()
        try:
            if method == "POST":
                resp = await client.post(path, json=json_body)
            else:
                resp = await client.get(path)
            duration_ms = (time.perf_counter() - start) * 1000
            return duration_ms, resp.status_code, resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            return duration_ms, 0, {"error": str(e)}

    async def _worker(self, client, method: str, path: str, json_body, results: list, count: int):
        for _ in range(count):
            dur, status, data = await self._make_request(client, method, path, json_body)
            if status == 200 or status == 202:
                results.append(dur)

    async def run_chat_benchmark(self, client):
        """Measure POST /api/chat latency."""
        warmup = self.args.warmup
        total = self.args.total
        concurrency = self.args.concurrency

        # warmup
        for _ in range(warmup):
            await self._make_request(client, "POST", "/api/chat", {"text": "benchmark warmup"})

        # run
        per_worker = total // concurrency
        tasks = [
            self._worker(client, "POST", "/api/chat",
                        {"text": "benchmark: measure latency distribution p50 p95 p99"},
                        self.results["chat"], per_worker)
            for _ in range(concurrency)
        ]
        await asyncio.gather(*tasks)

    async def run_state_benchmark(self, client):
        """Measure GET /api/state latency."""
        for _ in range(min(20, self.args.total // 5)):
            dur, _, _ = await self._make_request(client, "GET", "/api/state")
            self.results["state"].append(dur)

    async def run_memory_benchmark(self, client):
        """Measure memory tier API latency."""
        for _ in range(min(20, self.args.total // 5)):
            dur, _, _ = await self._make_request(client, "GET", "/api/memory/tiers")
            self.results["memory_tiers"].append(dur)

    async def run_policy_benchmark(self, client):
        """Measure policy state API latency."""
        for _ in range(min(20, self.args.total // 5)):
            dur, _, _ = await self._make_request(client, "GET", "/api/policy/state")
            self.results["policy_state"].append(dur)

    async def run_health_benchmark(self, client):
        """Measure health endpoints."""
        for _ in range(min(10, self.args.total // 10)):
            dur, _, _ = await self._make_request(client, "GET", "/health/live")
            self.results["health"].append(dur)
            dur, _, _ = await self._make_request(client, "GET", "/health/diagnostic")
            self.results["health"].append(dur)

    # ── In-process layer benchmarks ─────────────────────────

    def run_layer_benchmarks(self):
        """Direct Python benchmarks (bypass HTTP)."""
        results = {}

        # ── importance scorer ────────────────────────────────
        from memory.importance_scorer import ImportanceFeatures, ImportanceScorer
        scorer = ImportanceScorer()
        feats = ImportanceFeatures(
            user_explicit=True, goal_related=True, persona_related=True,
            source_reliability=0.9, emotional_intensity=0.5,
            self_model_delta=0.3, prediction_error=0.4,
        )
        start = time.perf_counter()
        for _ in range(10000):
            scorer.score(feats)
        results["importance_scorer"] = {
            "ops": 10000,
            "total_ms": round((time.perf_counter() - start) * 1000, 1),
            "unit": "score() calls",
        }

        # ── policy engine evaluate ───────────────────────────
        from core.policy_engine import PolicyEngine
        pe = PolicyEngine()
        start = time.perf_counter()
        for _ in range(5000):
            pe.evaluate("user_message", {"source": "user"})
        results["policy_evaluate"] = {
            "ops": 5000,
            "total_ms": round((time.perf_counter() - start) * 1000, 1),
            "unit": "evaluate() calls",
        }

        # ── world model upsert ───────────────────────────────
        from world.world_model import WorldModelGraph
        wm = WorldModelGraph()
        start = time.perf_counter()
        for i in range(5000):
            wm.upsert_entity("task", f"Bench task {i}", {"status": "active"})
        results["world_model_upsert"] = {
            "ops": 5000,
            "total_ms": round((time.perf_counter() - start) * 1000, 1),
            "unit": "upsert_entity() calls",
        }

        # ── entity extraction ────────────────────────────────
        from core.entity_extractor import entity_extractor
        text = "/task: benchmark performance test for EVA system\nTODO: optimize database queries\nassigned to @alice\ncritical: latency p99 too high"
        start = time.perf_counter()
        for _ in range(5000):
            entity_extractor.extract_entities(text)
        results["entity_extraction"] = {
            "ops": 5000,
            "total_ms": round((time.perf_counter() - start) * 1000, 1),
            "unit": "extract_entities() calls",
        }

        # ── memory tier ingest ───────────────────────────────
        from memory.tiered_store import TieredMemoryManager, SessionMemory
        from memory.sqlite_store import SQLiteStore
        import tempfile
        tmp = tempfile.mkdtemp()
        store = SQLiteStore(Path(os.path.join(tmp, "bench.db")))
        store.init_db()
        tm = TieredMemoryManager(store)

        start = time.perf_counter()
        for i in range(2000):
            tm.ingest(f"benchmark memory entry {i}", importance=0.5 + (i % 5) * 0.1, source="bench")
        results["memory_ingest"] = {
            "ops": 2000,
            "total_ms": round((time.perf_counter() - start) * 1000, 1),
            "unit": "ingest() calls",
        }

        # ── recall ───────────────────────────────────────────
        start = time.perf_counter()
        for _ in range(1000):
            tm.recall("benchmark", tiers=[1, 2, 3])
        results["memory_recall"] = {
            "ops": 1000,
            "total_ms": round((time.perf_counter() - start) * 1000, 1),
            "unit": "recall() calls",
        }

        # ── diagnostics ──────────────────────────────────────
        from runtime.diagnostics import SystemDiagnostic
        diag = SystemDiagnostic()
        state = {"pending_events": 0, "pending_results": 0, "policy_state": {"state_machine": {"current": "dormant"}}}
        start = time.perf_counter()
        for _ in range(100):
            diag.run_full(store, tm, state)
        results["diagnostics"] = {
            "ops": 100,
            "total_ms": round((time.perf_counter() - start) * 1000, 1),
            "unit": "run_full() calls",
        }

        self.layer_results = results

    # ── Report ──────────────────────────────────────────────

    @staticmethod
    def _stats(name: str, values: list[float]) -> dict:
        if not values:
            return {"name": name, "count": 0}
        s = sorted(values)
        n = len(s)
        return {
            "name": name,
            "count": n,
            "mean_ms": round(statistics.mean(s), 2),
            "p50_ms": round(s[n // 2], 2),
            "p95_ms": round(s[int(n * 0.95)], 2) if n >= 20 else round(s[-1], 2),
            "p99_ms": round(s[int(n * 0.99)], 2) if n >= 100 else round(s[-1], 2),
            "min_ms": round(s[0], 2),
            "max_ms": round(s[-1], 2),
            "throughput_rps": round(n / (sum(s) / 1000), 1) if sum(s) > 0 else 0,
        }

    def build_report(self) -> dict:
        api_stats = {}
        for name in ["chat", "state", "health", "memory_tiers", "policy_state"]:
            api_stats[name] = self._stats(name, self.results[name])

        layer_stats = {}
        for name, data in self.layer_results.items():
            layer_stats[name] = {
                "name": name,
                "ops": data["ops"],
                "total_ms": data["total_ms"],
                "avg_us": round(data["total_ms"] / data["ops"] * 1000, 1),
                "unit": data["unit"],
            }

        return {
            "config": {
                "total_requests": self.args.total,
                "concurrency": self.args.concurrency,
                "in_process": self.args.in_process,
            },
            "api_benchmarks": api_stats,
            "layer_benchmarks": layer_stats,
        }

    def print_report(self, report: dict):
        print("\n" + "=" * 70)
        print("EVA Performance Benchmark Report")
        print("=" * 70)
        print(f"\nConfig: total={report['config']['total_requests']}, "
              f"concurrency={report['config']['concurrency']}, "
              f"in_process={report['config']['in_process']}")

        print("\n── HTTP API Latency ──────────────────────────────────")
        for name, stats in report["api_benchmarks"].items():
            if stats["count"] > 0:
                print(f"  {name:20s}: mean={stats['mean_ms']:7.1f}ms  "
                      f"p50={stats['p50_ms']:7.1f}ms  p95={stats['p95_ms']:7.1f}ms  "
                      f"p99={stats['p99_ms']:7.1f}ms  "
                      f"rps={stats['throughput_rps']:6.1f}  (n={stats['count']})")

        if report["layer_benchmarks"]:
            print("\n── Internal Layer Performance ──────────────────────")
            for name, stats in report["layer_benchmarks"].items():
                print(f"  {name:25s}: {stats['avg_us']:8.1f}us/op  "
                      f"({stats['ops']} ops in {stats['total_ms']:.0f}ms)")

        # summary line
        chat = report["api_benchmarks"]["chat"]
        if chat["count"] > 0:
            print(f"\nSummary: chat p50={chat['p50_ms']}ms p95={chat['p95_ms']}ms "
                  f"throughput={chat['throughput_rps']}rps")
        print("=" * 70 + "\n")


# ── Main ────────────────────────────────────────────────────

async def main():
    args = parse_args()
    runner = BenchmarkRunner(args)

    # Layer benchmarks (no server needed)
    runner.run_layer_benchmarks()

    # HTTP benchmarks
    async with runner._client() as client:
        await runner.run_health_benchmark(client)
        await runner.run_state_benchmark(client)
        await runner.run_memory_benchmark(client)
        await runner.run_policy_benchmark(client)
        await runner.run_chat_benchmark(client)

    report = runner.build_report()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        runner.print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
