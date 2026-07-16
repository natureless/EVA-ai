import time


def test_manual_maintenance_can_trigger_proactive_reminder(client):
    client.post("/api/chat", json={"text": "Advance EVA proactive engine"})
    time.sleep(1.2)

    response = client.post("/api/debug/maintenance/trigger")
    assert response.status_code == 200

    time.sleep(0.3)

    state = client.get("/api/state").json()
    trace = client.get("/api/debug/trace?limit=10").json()["items"]
    memory = client.get("/api/memory/recent?limit=10").json()["items"]
    proactive = client.get("/api/proactive/state").json()

    assert state["last_proactive_reason"] in {
        "stagnation_detected",
        "below_threshold",
        "cooldown_active",
        "no_focus",
        "no_user_activity_baseline",
    }

    assert proactive["proactive_state"] is not None

    assert any(item["event_type"] in {"maintenance", "reminder_trigger"} for item in trace)
    assert any(item["event_type"] in {"user_message", "reminder_trigger"} for item in memory)
