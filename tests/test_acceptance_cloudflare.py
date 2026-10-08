"""Acceptance must reject partial delivery and never retry a disconnected write."""
import json

import httpx
import pytest

from scripts.acceptance_cloudflare import chat_check


@pytest.mark.parametrize("disconnect", [False, True])
def test_chat_acceptance_polls_same_task_without_resubmitting(disconnect):
    calls = []
    report = {"chat_requests": []}
    saved = []
    result = {}

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["remember"] is False
            # Attempt identity is recorded before issuing the potentially ambiguous write.
            assert saved[0]["marker"] == payload["source_event_id"]
            result.update(task_id="retained-task", reply=payload["source_event_id"],
                          ok=True, completed=True, terminal_state="succeeded")
            content = "event: result\ndata: " + json.dumps(result) + "\n\ndata: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream", "x-eva-task-id": "retained-task"}, text=content)
        assert request.url.path == "/api/chat/result/retained-task"
        return httpx.Response(200, json=result)

    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handler)) as client:
        value = chat_check(client, disconnect, report, lambda: saved.append(dict(report["chat_requests"][0])))
    assert value["completed"] is True
    assert calls == [("POST", "/api/chat/stream"), ("GET", "/api/chat/result/retained-task")]
    assert saved[1]["task_id"] == "retained-task"


@pytest.mark.parametrize("failure", ["missing_done", "login_html", "failed_receipt", "wrong_reply"])
def test_chat_acceptance_rejects_incomplete_or_unsuccessful_delivery(failure):
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(json.loads(request.content))
        if failure == "login_html":
            return httpx.Response(200, headers={"content-type": "text/html"}, text="Sign in")
        receipt = {"task_id": "t", "completed": True, "ok": failure != "failed_receipt",
                   "terminal_state": "failed" if failure == "failed_receipt" else "succeeded",
                   "reply": "wrong" if failure == "wrong_reply" else posts[0]["source_event_id"]}
        if request.method == "GET":
            return httpx.Response(200, json=receipt)
        content = "event: result\ndata: " + json.dumps(receipt) + "\n\n"
        if failure != "missing_done":
            content += "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream", "x-eva-task-id": "t"}, text=content)

    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AssertionError):
            chat_check(client, False, {"chat_requests": []})
    assert len(posts) == 1
