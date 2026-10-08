"""Local UI preview with explicit fixture replies; no database, model or EVA runtime.

Run: python scripts/preview_cube_chat.py --port 8776
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="EVA cube UI preview")
app.mount("/static", StaticFiles(directory=ROOT / "ui/web/static"), name="static")
templates = Jinja2Templates(directory=ROOT / "ui/web/templates")
REPLIES = {
    "normal": "这是正常对话预览，未连接模型，也不会保存消息。\n\n你可以继续测试二阶魔方的对话动效与布局。",
    "deep": "这是深度思考预览，未连接模型，也不会保存消息。\n\n你可以继续测试三阶魔方的分析动效与布局。",
}
_receipts: dict[str, dict] = {}


class PreviewChatRequest(BaseModel):
    text: str = Field(default="", max_length=20000)
    remember: bool = False
    mode: str = "normal"


def _mode(value: str) -> str:
    return "deep" if value == "deep" else "normal"


def _reply(mode: str) -> str:
    return REPLIES[_mode(mode)]


def _fixture_receipt(mode: str) -> dict:
    task_id = f"ui-preview-{uuid.uuid4().hex[:10]}"
    receipt = {"completed": True, "ok": True, "terminal_state": "succeeded", "task_id": task_id,
               "reply": _reply(mode), "mode": mode, "mode_info": {"strategy": "preview"}}
    _receipts[task_id] = receipt
    if len(_receipts) > 128:
        _receipts.pop(next(iter(_receipts)))
    return receipt


@app.get("/")
@app.get("/chat")
def page(request: Request):
    return templates.TemplateResponse(request=request, name="chat.html",
                                      context={"app_name": "EVA · 交互预览", "preview_mode": True})


@app.get("/settings")
def settings_page(request: Request):
    return templates.TemplateResponse(request=request, name="settings.html",
                                      context={"app_name": "EVA · 交互预览", "preview_mode": True})


@app.get("/memory")
def graph_page(request: Request):
    return templates.TemplateResponse(request=request, name="memory_graph.html",
                                      context={"app_name": "EVA · UI 示例", "preview_mode": True,
                                               "read_only_preview": True})


@app.get("/memory/list")
@app.get("/mvsc")
@app.get("/dashboard")
@app.get("/unavailable")
def other_page(request: Request):
    names = {"/memory/list": "memory.html", "/mvsc": "mvsc_dashboard.html",
             "/dashboard": "dashboard.html", "/unavailable": "service_unavailable.html"}
    return templates.TemplateResponse(request=request, name=names[request.url.path],
                                      context={"app_name": "EVA · UI 示例", "preview_mode": True,
                                               "read_only_preview": True})


def _graph_fixture(query: str = "") -> dict:
    nodes = [{"id": f"S{i % 5 + 1}:demo-{i}", "record_id": f"demo-{i}", "tier": f"S{i % 5 + 1}",
              "label": ["模块化标识", "对话空间", "品牌规则", "世界关系", "交互记录"][i % 5] + f" · {i + 1}",
              "content": "仅用于界面预览的合成示例，不属于实际记忆记录。",
              "provenance": {"epistemic_status": "simulation", "source": "UI fixture"}} for i in range(75)]
    edges = [{"source": nodes[i]["id"], "target": nodes[(i + 1) % len(nodes)]["id"],
              "relation": "界面示例关联", "kind": "stored_relation"} for i in range(len(nodes))]
    edges += [{"source": nodes[i]["id"], "target": nodes[(i + 7) % len(nodes)]["id"],
               "relation": "示例来源", "kind": "provenance"} for i in range(0, len(nodes), 3)]
    filtered = [n for n in nodes if query.lower() in n["label"].lower()]
    ids = {n["id"] for n in filtered}
    return {"preview": True, "nodes": filtered,
            "edges": [e for e in edges if e["source"] in ids and e["target"] in ids], "query": query,
            "counts": {f"S{i}": {"available": True, "total": 15} for i in range(1, 6)},
            "scope": {"matched_nodes": len(filtered), "nodes_truncated": False, "edges_truncated": False},
            "generated_at": datetime.now(timezone.utc).isoformat(), "warnings": [], "obsidian": {}}


@app.get("/api/memory/graph")
def graph_data(q: str = ""):
    return _graph_fixture(q)


@app.get("/api/memory/graph/node")
def graph_node(tier: str, record_id: str):
    node = next((n for n in _graph_fixture()["nodes"] if n["tier"] == tier and n["record_id"] == record_id), None)
    return node or JSONResponse({"error": "fixture_not_found"}, status_code=404)


@app.get("/api/memory/tiers")
def memory_tiers():
    return {"tiers": [{"name": f"S{i}", "label": "UI 示例", "entries": 15,
                       "max": 100, "ttl": "仅预览", "storage": "合成数据"} for i in range(1, 6)]}


@app.get("/api/memory/entries/{tier}")
def memory_entries(tier: str):
    return {"entries": [], "total": 0}


@app.get("/api/runtime")
def runtime():
    return {"preview": True}


@app.get("/api/chat/modes")
def modes():
    return {"modes": [
        {"mode": "normal", "order": 2, "strategy": "preview", "label": "正常对话"},
        {"mode": "deep", "order": 3, "strategy": "preview", "label": "深度思考"},
    ]}


@app.post("/api/chat/stream")
async def stream(payload: PreviewChatRequest):
    mode = _mode(payload.mode)
    receipt = _fixture_receipt(mode)

    async def events():
        for text in (["这是正常对话预览，", "未连接模型，", "也不会保存消息。"]
                     if mode == "normal" else ["这是深度思考预览，", "未连接模型，", "也不会保存消息。"]):
            yield f"data: {text}\n\n"
            await asyncio.sleep(0.4)
        yield f"event: result\ndata: {json.dumps(receipt)}\n\ndata: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"X-EVA-Task-ID": receipt["task_id"]})


@app.post("/api/chat")
def chat(payload: PreviewChatRequest):
    mode = _mode(payload.mode)
    receipt = _fixture_receipt(mode)
    return {"accepted": True, "task_id": receipt["task_id"], "request_deadline_sec": 5, "mode": mode}


@app.get("/api/chat/result/ui-preview")
def legacy_result():
    return {"completed": True, "task_id": "ui-preview", "reply": _reply("normal"), "mode": "normal",
            "mode_info": {"strategy": "preview"}}


@app.get("/api/chat/result/{task_id}")
def result(task_id: str):
    if task_id not in _receipts:
        return JSONResponse({"completed": False, "task_id": task_id,
                             "error": "result_unknown_or_expired"}, status_code=404)
    return _receipts[task_id]


@app.websocket("/ws")
async def websocket(socket: WebSocket):
    await socket.accept()
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8776)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port)
