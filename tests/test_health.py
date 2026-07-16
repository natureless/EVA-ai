def test_health_live(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "alive"


def test_health_ready(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "ready"
    assert data["components"]["db"] is True
    assert data["components"]["event_bus"] is True
    assert data["components"]["planner"] is True
    assert data["components"]["registry"] is True
    assert data["components"]["result_registry"] is True
    assert data["components"]["snapshot"] is True
    assert data["components"]["profile"] is True
    assert data["components"]["persona"] is True
    assert data["components"]["self_model"] is True
    assert data["components"]["scheduler"] is True
    assert data["components"]["proactive"] is True
    assert data["components"]["cognition_loop"] is True
