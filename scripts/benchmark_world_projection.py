"""Compare full-snapshot context reads with bounded world projections, offline."""

import argparse
import gc
import json
import math
from pathlib import Path
import platform
import statistics
import time
import tracemalloc

from world.world_model import WorldModelGraph


def benchmark(model: WorldModelGraph, *, iterations: int = 30) -> dict:
    if type(iterations) is not int or iterations < 2:
        raise ValueError("iterations must be an integer >= 2")

    def full_snapshot_projection():
        world = model.to_dict()
        return {
            "focus": world["focus"][:160], "mode": world["mode"],
            "active_tasks": world["active_tasks"][:10],
            "recent_entities": world["recent_entities"][:10],
            "last_loop_id": world["last_loop_id"],
        }

    result = {
        "schema_version": 1, "python": platform.python_version(),
        "platform": platform.system(), "graph": model.counts(), "iterations": iterations,
        "scope": "In-process context read only; graph creation, startup, DB IO and LLM costs excluded. No production latency claim.",
    }
    for label, call in (("full_snapshot_then_slice", full_snapshot_projection), ("bounded_projection", model.context_projection)):
        call()
        timings = []
        for _ in range(iterations):
            start = time.perf_counter()
            call()
            timings.append((time.perf_counter() - start) * 1000)
        gc.collect()
        tracemalloc.start()
        try:
            view = call()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        result[label] = {
            "median_ms": statistics.median(timings),
            "p95_ms": sorted(timings)[math.ceil(iterations * 0.95) - 1],
            "peak_traced_bytes": peak,
            "returned_json_bytes": len(json.dumps(view, ensure_ascii=False).encode("utf-8")),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, help="optional snapshot, decoded without writes")
    parser.add_argument("--entities", type=int, default=1000)
    parser.add_argument("--edges", type=int, default=100000)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.snapshot:
        data = json.loads(args.snapshot.read_bytes())
        model = WorldModelGraph.from_dict(data["world_model"])
        dataset = "snapshot_decoded_by_current_restore_rules"
    else:
        if args.entities < 1 or args.edges < 0:
            parser.error("entities must be positive and edges nonnegative")
        model = WorldModelGraph()
        for i in range(args.entities):
            model.upsert_entity("task", f"Task {i}", {"status": "active"})
        for i in range(args.edges):
            model.link(f"source_{i}", f"task_task_{i % args.entities}", "references")
        dataset = "synthetic_unique_edges"
    result = {"dataset": dataset, **benchmark(model, iterations=args.iterations)}
    raw = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(raw)
    print(raw)


if __name__ == "__main__":
    main()
