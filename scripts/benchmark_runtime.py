"""Offline HTTP wiring baseline. Each mode/pass uses a fresh process and temp data.

Run: python scripts/benchmark_runtime.py --output artifacts/base02-runtime.json
Latency is unprofiled; Python allocation peak uses a separate tracemalloc pass.
This measures in-process ASGI/HTTP overhead, not TCP, model quality or real LLMs.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MODES = ("legacy", "minimal", "mvsc_attached_legacy")


@contextmanager
def isolated_workspace():
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="eva-runtime-baseline-") as temporary:
        try:
            os.chdir(temporary)
            yield Path(temporary)
        finally:
            os.chdir(previous)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {"count": len(values), "mean": sum(values) / len(values),
            "p50": ordered[math.ceil(len(values) * .50) - 1],
            "p95": ordered[math.ceil(len(values) * .95) - 1], "max": ordered[-1]}


def run_child(manifest: dict, mode: str, memory_pass: bool) -> dict:
    # No inherited application overrides or credentials, and no .env imports.
    for key in list(os.environ):
        if key.startswith("EVA_") or key in {"OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"}:
            del os.environ[key]
    os.environ.update(EVA_LLM_PROVIDER="mock", EVA_API_TOKEN="baseline-token")
    sys.path.insert(0, str(ROOT))
    random.seed(manifest["seed"])
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    import app.config
    from core.llm_adapter import get_llm, MockLLM
    assert isinstance(get_llm(), MockLLM)

    blocked_network = []
    def forbid_network(event, args):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            # Windows implements asyncio's wakeup socketpair over numeric loopback.
            host = args[1][0] if event == "socket.connect" else args[0]
            try:
                if ipaddress.ip_address(host).is_loopback:
                    return
            except ValueError:
                pass
            blocked_network.append(event)
            raise RuntimeError("external network access forbidden in offline baseline")
    sys.addaudithook(forbid_network)

    with isolated_workspace() as work:
        # Relative experimental config lookup sees an empty controlled workspace.
        os.chdir(work)
        paths = dict(
            base_dir=work, data_dir=work / "data", log_dir=work / "logs",
            db_path=work / "data/eva.db", snapshot_dir=work / "data/snapshots",
            latest_snapshot_path=work / "data/snapshots/latest.json",
            profile_path=work / "data/profile.json", self_model_path=work / "data/self_model.json",
            vector_index_path=work / "data/vector_index.faiss",
            ui_dir=ROOT / "ui/web", template_dir=ROOT / "ui/web/templates", static_dir=ROOT / "ui/web/static",
        )
        config = app.config.Settings(
            **manifest["settings"], **paths, enable_minimal_brain=mode == "minimal",
            enable_mvsc_pipeline=mode == "mvsc_attached_legacy",
        )
        app.config.settings = config
        with patch("dotenv.load_dotenv", return_value=False):
            import app.main
        import tracemalloc
        if memory_pass:
            tracemalloc.start()
        start = time.perf_counter()
        with TestClient(app.main.app, headers={"X-API-Token": "baseline-token"}) as client:
            bootstrap_ms = (time.perf_counter() - start) * 1000
            container = client.app.state.container
            observation = client.get("/api/runtime").json()
            expected = "minimal" if mode == "minimal" else "legacy"
            assert observation["mode"] == expected and observation["consumer_running"]
            assert observation["extensions"]["mvsc"]["attached"] == (mode == "mvsc_attached_legacy")
            assert container.github_poller is None and container.vector_store is None
            for name in ("data_dir", "db_path", "latest_snapshot_path", "profile_path", "self_model_path"):
                assert getattr(config, name).is_relative_to(work)

            def request_message(message):
                start = time.perf_counter()
                response = client.post("/api/chat", json={"text": message})
                ack_ms = (time.perf_counter() - start) * 1000
                assert response.status_code == 200, response.text
                ack = response.json()
                assert ack["accepted"]
                deadline = time.monotonic() + 30
                poll_delay = manifest["poll_initial_sec"]
                while time.monotonic() < deadline:
                    response = client.get(f"/api/chat/result/{ack['task_id']}")
                    if response.status_code == 200:
                        break
                    assert response.status_code == 202, response.text
                    time.sleep(poll_delay)
                    poll_delay = min(poll_delay * 2, manifest["poll_max_sec"])
                else:
                    raise RuntimeError("HTTP receipt deadline exceeded")
                result = response.json()
                assert result["completed"] and result["ok"], result
                return {"ack_ms": ack_ms, "http_terminal_ms": (time.perf_counter() - start) * 1000,
                        **result["timing"]}

            for message in manifest["warmup"]:
                request_message(message)
            before = container.runtime.processor.stats["total_processed"]
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=manifest["concurrency"]) as executor:
                samples = list(executor.map(request_message, manifest["messages"]))
            workload_ms = (time.perf_counter() - start) * 1000
            # Receipt completion may precede consumer bookkeeping by one callback.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                after = client.get("/api/runtime").json()
                if after["execution_idle"] and not after["consumer"].get("processor_busy", False):
                    break
                time.sleep(.005)
            assert container.runtime.processor.stats["total_processed"] - before == len(samples)
            assert len(container.store.fetchall("SELECT * FROM traces")) == len(samples) + len(manifest["warmup"])
            # Serve the real shipped template and script, not the test fixture's dummy UI.
            for url in ("/", "/static/runtime_status.js"):
                page = client.get(url)
                assert page.status_code == 200
                assert ("runtimeStatus" if url == "/" else "EvaRuntimeStatus") in page.text
            peak = tracemalloc.get_traced_memory()[1] if memory_pass else None
            assert not blocked_network, "a component attempted external network access"
            # Normalize volatile paths; never dump environment variables or live state.
            normalized = config.model_dump(mode="json")
            for name in paths:
                normalized[name] = "<shipped-ui>" if name in {"ui_dir", "template_dir", "static_dir"} else "<temporary>/" + name
            normalized["llm_provider"] = "mock"
            report = {
                "scenario": mode, "pass": "memory" if memory_pass else "latency",
                "actual_mode": observation["mode"], "consumer_type": observation["consumer_type"],
                "processor_type": observation["processor_type"], "mvsc": observation["extensions"]["mvsc"],
                "effective_config": normalized,
                "effective_config_sha256": digest(json.dumps(normalized, sort_keys=True).encode()),
                "requests": len(samples), "warmup_requests": len(manifest["warmup"]),
                "bootstrap_ms": bootstrap_ms, "workload_ms": workload_ms,
                "python_peak_bytes": peak, "samples": samples,
                "metrics": {key: distribution([sample[key] for sample in samples]) for key in
                            ("ack_ms", "http_terminal_ms", "admission_to_execution_ms", "admission_to_terminal_ms")},
                "rejected_full": after["event_bus"]["rejected_full"],
                "receipt_deadline_expirations": container.result_registry.stats()["deadline_expirations"],
            }
        assert container.system_state["shutdown_status"] == "complete"
        if memory_pass:
            tracemalloc.stop()
        # Windows cannot remove the cwd; release it before temporary cleanup.
        os.chdir(ROOT)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "configs/baselines/runtime-v1.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--child-mode", choices=MODES, help=argparse.SUPPRESS)
    parser.add_argument("--memory-pass", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    manifest_bytes = args.manifest.resolve().read_bytes()
    output = args.output.resolve()
    manifest = json.loads(manifest_bytes)
    if args.child_mode:
        report = run_child(manifest, args.child_mode, args.memory_pass)
    else:
        passes = []
        with tempfile.TemporaryDirectory(prefix="eva-runtime-results-") as temp:
            for mode in MODES:
                for memory_pass in (False, True):
                    child_output = Path(temp) / f"{mode}-{memory_pass}.json"
                    command = [sys.executable, str(Path(__file__).resolve()), "--manifest", str(args.manifest.resolve()),
                               "--output", str(child_output), "--child-mode", mode]
                    if memory_pass:
                        command.append("--memory-pass")
                    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                                            env={**os.environ, "PYTHONHASHSEED": str(manifest["seed"]), "PYTHONUTF8": "1"})
                    if result.returncode:
                        raise RuntimeError(f"{mode} pass failed:\n{result.stdout}\n{result.stderr}")
                    passes.append(json.loads(child_output.read_text(encoding="utf-8")))
                    print(f"{mode}: {'memory' if memory_pass else 'latency'} pass complete", flush=True)
        source_hash = hashlib.sha256()
        for folder in ("app", "core", "runtime", "event", "agents", "agent_os", "memory", "world", "persona", "packages", "connectors", "scripts", "configs", "ui", "migrations"):
            for path in sorted((ROOT / folder).rglob("*")):
                if path.is_file() and path.suffix in {".py", ".json", ".yaml", ".yml", ".js", ".html", ".sql"}:
                    source_hash.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
        report = {
            "schema_version": 1, "manifest_sha256": digest(manifest_bytes), "source_sha256": source_hash.hexdigest(),
            "python": platform.python_version(), "platform": platform.system(), "machine": platform.machine(),
            "dependencies": {name: importlib.metadata.version(name) for name in ("fastapi", "httpx", "pydantic", "starlette")},
            "definitions": {
                "admission_to_execution_ms": "monotonic receipt reservation to first processor claim; includes EventBus and Minimal scheduling waits",
                "http_terminal_ms": "ASGI TestClient POST start to terminal GET response; includes polling and middleware, excludes TCP",
                "python_peak_bytes": "separate tracemalloc pass; bootstrap plus warmup and workload; excludes imports and native/RSS allocations",
                "percentiles": "nearest rank; small fixed fixture, not a capacity or quality claim",
            }, "manifest": manifest, "passes": passes,
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
