def test_persona_active_defaults(client):
    response = client.get("/api/persona/active")
    assert response.status_code == 200
    persona = response.json()["persona"]
    assert persona["name"] == "EVA"
    assert persona["version"] >= 1


def test_persona_update_persists(client):
    initial = client.get("/api/persona/active").json()["persona"]
    response = client.post(
        "/api/persona/update",
        json={
            "tone_style": "direct",
            "soft_preferences": ["clarity", "brevity"],
        },
    )
    assert response.status_code == 200
    updated = response.json()["persona"]
    assert updated["tone_style"] == "direct"
    assert "clarity" in updated["soft_preferences"]
    assert updated["version"] == initial["version"] + 1

    roundtrip = client.get("/api/persona/active").json()["persona"]
    assert roundtrip["tone_style"] == "direct"
