"""Repeatable production-origin checks; never claims browser/phone acceptance.

Default is read-only. --chat submits TWO marked messages (normal SSE and
disconnect/poll recovery), may invoke the configured model and leaves events.
API credentials are sent only to the fixed loopback origin, never to Cloudflare.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZipFile

from dotenv import dotenv_values
import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:8000"
PUBLIC_PATHS = ("/", "/api/memory/graph", "/api/obsidian/export", "/ws")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def checked_json(client, path, **params):
    response = client.get(path, params=params)
    require(response.status_code == 200, f"unexpected HTTP {response.status_code}")
    require("no-store" in response.headers.get("cache-control", ""), "missing no-store")
    return response.json()


def poll_result(client, task_id, deadline):
    while time.monotonic() < deadline:
        response = client.get(f"/api/chat/result/{task_id}")
        require(response.status_code in (200, 202), f"receipt HTTP {response.status_code}")
        data = response.json()
        if data.get("completed"):
            return data
        time.sleep(0.5)
    raise AssertionError("receipt wait expired; request may still be running; do not resubmit")


def chat_check(client, disconnect, report, persist=lambda: None):
    marker = "EVA_ACCEPTANCE_" + uuid4().hex
    entry = {"marker": marker, "disconnect": disconnect, "remember": False}
    report["chat_requests"].append(entry)
    persist()
    payload = {"text": f"接口验收测试：请仅回复 {marker}，不要调用工具或执行其他任务。",
               "source_event_id": marker, "remember": False}
    deadline = time.monotonic() + 50
    receipt = None
    done = False
    with client.stream("POST", "/api/chat/stream", json=payload) as response:
        entry["http_status"] = response.status_code
        require(response.status_code == 200, f"SSE HTTP {response.status_code}; do not resubmit")
        require("text/event-stream" in response.headers.get("content-type", ""), "not SSE")
        task_id = response.headers.get("x-eva-task-id")
        entry["task_id"] = task_id
        persist()
        require(bool(task_id), "SSE task ID missing; do not resubmit")
        if not disconnect:
            event = ""
            for line in response.iter_lines():
                require(time.monotonic() < deadline, "SSE deadline exceeded; do not resubmit")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line == "data: [DONE]":
                    done = True
                    break
                elif line.startswith("data:") and event == "result":
                    receipt = json.loads(line[5:].strip())
                elif not line:
                    event = ""
            require(done and receipt is not None, "SSE did not deliver receipt and DONE")
    # For disconnect mode this is the SAME request, never a second POST.
    polled = poll_result(client, task_id, deadline)
    entry.update(terminal_state=polled.get("terminal_state"), completed=polled.get("completed"),
                 reply_matches=polled.get("reply", "").strip() == marker)
    require(polled.get("ok") is True and polled.get("terminal_state") == "succeeded", "request failed")
    require(entry["reply_matches"], "model did not return the test marker")
    if not disconnect:
        require(receipt.get("task_id") == task_id and receipt.get("reply") == polled.get("reply"), "SSE/poll mismatch")
    return {"completed": True, "same_task_polled": True, "sse_done": done if not disconnect else None}


def run(args):
    env = dotenv_values(ROOT / ".env.cloudflare")
    token = env.get("EVA_API_TOKEN")
    require(bool(token), "missing EVA_API_TOKEN in .env.cloudflare")
    public = env.get("EVA_PUBLIC_ORIGIN", "")
    address = urlsplit(public)
    require(address.scheme == "https" and address.hostname and not address.username and not address.password
            and not address.query and not address.fragment and address.path in ("", "/"), "invalid public origin")
    team = urlsplit(env.get("EVA_CF_ACCESS_TEAM_DOMAIN", "")).hostname
    require(bool(team), "missing Access team HTTPS address")
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "checks": {}, "chat_requests": [],
              "pending": ["Public authenticated browser interaction", "Actual login expiration in browser",
                          "Physical phone", "Windows login/crash recovery"]}

    def persist():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def check(name, action):
        try:
            report["checks"][name] = {"status": "passed", "details": action()}
        except Exception as exc:
            # Avoid persisting server bodies, headers, login URLs, cookies, or credential-bearing exceptions.
            report["checks"][name] = {"status": "failed", "error_type": type(exc).__name__,
                                      "detail": str(exc) if isinstance(exc, AssertionError) else "See service diagnostics; response omitted"}
        persist()
        print(name, report["checks"][name]["status"], flush=True)

    with httpx.Client(base_url=ORIGIN, headers={"X-API-Token": token}, trust_env=False,
                      follow_redirects=False, timeout=20) as local, httpx.Client(trust_env=False,
                      follow_redirects=False, timeout=20) as anonymous:
        def readiness():
            data = checked_json(local, "/health/ready")
            require(data.get("status") == "ready", "runtime not ready")
            return {"ready": True}
        check("origin_readiness", readiness)

        def assets():
            page = local.get("/")
            require(page.status_code == 200, "homepage failed")
            result = {}
            for name in ("memory_graph.js", "memory_graph.css", "http.js", "memory_diagnostics.js"):
                response = local.get("/static/" + name)
                content = (ROOT / "ui/web/static" / name).read_bytes()
                require(response.status_code == 200 and response.content == content, f"{name} mismatch")
                require("/static/" + name in page.text, f"{name} not referenced")
                result[name] = hashlib.sha256(content).hexdigest()
            for element in ("localRelation", "localPath", "relationInspector"):
                require(f'id="{element}"' in page.text, f"missing {element}")
            return result
        check("origin_assets", assets)

        def graph():
            data = checked_json(local, "/api/memory/graph")
            nodes = {n["id"]: n for n in data["nodes"]}
            require(bool(nodes), "no nodes available for graph check")
            require(all(e["source"] in nodes and e["target"] in nodes for e in data["edges"]), "dangling displayed edge")
            node = next((n for n in nodes.values() if n["tier"] == "S4"), next(iter(nodes.values())))
            detail = checked_json(local, "/api/memory/graph/node", tier=node["tier"], record_id=node["record_id"])
            require(detail["record_id"] == node["record_id"], "wrong detail record")
            neighbors = checked_json(local, "/api/memory/graph/neighbors", tier=node["tier"], record_id=node["record_id"])
            require(neighbors["center"] == node["id"], "wrong neighbor center")
            return {"nodes": len(nodes), "edges": len(data["edges"]), "detail_ok": True,
                    "neighbor_page_edges": len(neighbors["edges"]), "next_offset": neighbors.get("next_offset")}
        check("origin_graph_and_neighbors", graph)

        def export():
            response = local.get("/api/obsidian/export", params={"limit": 420})
            require(response.status_code == 200 and "zip" in response.headers.get("content-type", ""), "export not ZIP")
            require("no-store" in response.headers.get("cache-control", ""), "export cache header missing")
            with ZipFile(BytesIO(response.content)) as archive:
                require(archive.testzip() is None, "ZIP integrity failed")
                require(bool(archive.namelist()), "empty ZIP")
                return {"entries": len(archive.namelist()), "crc_valid": True, "bytes": len(response.content)}
        check("origin_export", export)

        def protection(path, remote):
            response = anonymous.get((public.rstrip("/") if remote else ORIGIN) + path)
            if remote:
                location = urlsplit(response.headers.get("location", ""))
                require(response.status_code in (302, 303) and location.scheme == "https" and location.hostname == team,
                        f"unexpected anonymous public response {response.status_code}")
            else:
                require(response.status_code == 401, f"anonymous origin response {response.status_code}")
            return {"http_status": response.status_code, "access_redirect": remote}
        for path in PUBLIC_PATHS:
            check("anonymous_public:" + path, lambda path=path: protection(path, True))
            check("anonymous_origin:" + path, lambda path=path: protection(path, False))

        def websocket():
            # Reconnect a transport twice without publishing a business event.
            for _ in range(2):
                with connect("ws://127.0.0.1:8000/ws?channel=acceptance", additional_headers={"X-API-Token": token},
                             proxy=None, open_timeout=10, close_timeout=3) as ws:
                    ws.send("ping")
                    require(json.loads(ws.recv(timeout=10)).get("pong") is True, "WebSocket pong missing")
            return {"connections": 2, "pong_received": 2}
        check("origin_websocket_reconnect", websocket)
        if args.chat:
            check("origin_sse_and_poll", lambda: chat_check(local, False, report, persist))
            check("origin_disconnect_then_poll", lambda: chat_check(local, True, report, persist))
        else:
            report["pending"].append("Live chat disabled; use --chat for two marked test messages")
    failed = [name for name, result in report["checks"].items() if result["status"] == "failed"]
    report.update(finished_at=datetime.now(timezone.utc).isoformat(), failed_checks=failed,
                  status="failed_checks" if failed else "automated_checks_passed_browser_pending")
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat", action="store_true", help="Send two marked production messages; invokes model and records events")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/cloudflare-acceptance-latest.json")
    raise SystemExit(run(parser.parse_args()))
