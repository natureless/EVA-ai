import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import List

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def wait_for_ready(base_url: str, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    async with httpx.AsyncClient(base_url=base_url, timeout=2.0) as client:
        while time.time() < deadline:
            try:
                resp = await client.get("/health/ready")
                if resp.status_code == 200 and resp.json().get("status") == "ready":
                    return True
            except httpx.HTTPError:
                await asyncio.sleep(0.2)
            await asyncio.sleep(0.2)
    return False


async def run_load(client: httpx.AsyncClient, total: int, concurrency: int) -> dict:
    latencies: List[float] = []
    errors = 0

    semaphore = asyncio.Semaphore(concurrency)

    async def worker(idx: int) -> None:
        nonlocal errors
        payload = {"text": f"load-test message {idx}"}
        async with semaphore:
            start = time.perf_counter()
            try:
                resp = await client.post("/api/chat", json=payload)
                if resp.status_code not in (200, 202):
                    errors += 1
                latencies.append((time.perf_counter() - start) * 1000)
            except httpx.HTTPError:
                errors += 1

    tasks = [worker(i) for i in range(total)]
    await asyncio.gather(*tasks)

    latencies_sorted = sorted(latencies)
    p50 = percentile(latencies_sorted, 50)
    p95 = percentile(latencies_sorted, 95)
    p99 = percentile(latencies_sorted, 99)
    return {
        "total": total,
        "errors": errors,
        "latencies_ms": {
            "min": min(latencies_sorted) if latencies_sorted else None,
            "max": max(latencies_sorted) if latencies_sorted else None,
            "mean": statistics.mean(latencies_sorted) if latencies_sorted else None,
            "p50": p50,
            "p95": p95,
            "p99": p99,
        },
    }


def percentile(values: List[float], pct: int) -> float | None:
    if not values:
        return None
    k = (len(values) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[int(k)]
    return values[f] + (values[c] - values[f]) * (k - f)


def start_server(port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        stdout=None,
        stderr=None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple async load test for EVA.")
    parser.add_argument("--total", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--ready-timeout", type=float, default=30.0)
    parser.add_argument("--in-process", action="store_true")
    parser.add_argument("--no-server", action="store_true")
    args = parser.parse_args()

    base_url = f"http://127.0.0.1:{args.port}"

    if args.in_process:
        results = asyncio.run(run_in_process(args.total, args.concurrency, args.timeout))
        print(json.dumps(results, indent=2))
        return

    proc = None
    if not args.no_server:
        proc = start_server(args.port)

    try:
        ready = asyncio.run(wait_for_ready(base_url, timeout_s=args.ready_timeout))
        if not ready:
            raise RuntimeError("server not ready")

        async def run_external() -> dict:
            async with httpx.AsyncClient(base_url=base_url, timeout=args.timeout) as client:
                return await run_load(client, args.total, args.concurrency)

        results = asyncio.run(run_external())
        print(json.dumps(results, indent=2))
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


async def run_in_process(total: int, concurrency: int, timeout_s: float) -> dict:
    from app.main import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=timeout_s,
        ) as client:
            return await run_load(client, total, concurrency)


if __name__ == "__main__":
    main()
