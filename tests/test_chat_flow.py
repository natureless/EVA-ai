import time


def test_dashboard_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "EVA" in response.text


def test_chat_returns_reply(client):
    response = client.post("/api/chat", json={"text": "Continue designing EVA world graph"})
    assert response.status_code == 200

    data = response.json()
    assert data["accepted"] is True
    assert data["completed"] is True
    assert "reply" in data
    assert data["selected_agent"] == "chat_agent"


def test_docs_routing(client):
    response = client.post("/api/chat", json={"text": "Please summarize this document"})
    assert response.status_code == 200

    data = response.json()
    assert data["completed"] is True
    assert data["selected_agent"] == "docs_agent"


def test_search_routing(client):
    response = client.post("/api/chat", json={"text": "/search EVA"})
    assert response.status_code == 200

    data = response.json()
    assert data["completed"] is True
    assert data["selected_agent"] == "search_agent"


def test_coding_routing(client):
    response = client.post("/api/chat", json={"text": "/code README.md"})
    assert response.status_code == 200

    data = response.json()
    assert data["completed"] is True
    assert data["selected_agent"] == "coding_agent"


def test_state_updates_after_chat(client):
    client.post("/api/chat", json={"text": "Advance EVA scheduler"})
    time.sleep(0.1)

    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()

    assert "EVA scheduler" in data["focus"]
    assert data["last_selected_agent"] == "chat_agent"
    assert data["last_reply"] != ""


def test_memory_and_trace_written(client):
    client.post("/api/chat", json={"text": "Advance EVA snapshot"})
    time.sleep(0.1)

    mem = client.get("/api/memory/recent?limit=5")
    trace = client.get("/api/debug/trace?limit=5")
    events = client.get("/api/events/recent?limit=5")

    assert mem.status_code == 200
    assert trace.status_code == 200
    assert events.status_code == 200

    mem_items = mem.json()["items"]
    trace_items = trace.json()["items"]
    event_items = events.json()["items"]

    assert len(mem_items) >= 1
    assert len(trace_items) >= 1
    assert len(event_items) >= 1

    assert any(item["event_type"] == "user_message" for item in mem_items)
    assert any(item["event_type"] == "user_message" for item in trace_items)
    assert any(item["type"] == "user_message" for item in event_items)
