from agents.base_agent import AgentTask
from agents.chat_agent import ChatAgent


def test_cognitive_context_reaches_model_without_full_graph(monkeypatch):
    captured = []

    class RecordingLLM:
        provider = "test"

        def chat(self, messages):
            captured.extend(messages)
            return "A response based on the current task."

    monkeypatch.setattr("agents.chat_agent.get_llm", lambda: RecordingLLM())
    agent = ChatAgent()
    agent.run(AgentTask(kind="chat", payload={
        "text": "Continue the current task",
        "context": {"minimal_brain": {
            "state_version": 17,
            "goal": {"event_id": "event-1", "summary": "Inspect local state"},
            "body": {"pressure": 0.8},
            "workspace": [{"event_id": "event-1", "summary": "Inspect local state"}],
            "recent_feedback": [{"event_id": "prior", "ok": True, "summary": "Earlier hypothesis"}],
            "models": {"world": "UNBOUNDED_GRAPH_MUST_NOT_ENTER_PROMPT"},
        }},
    }))
    system = captured[0]["content"]
    assert '"state_version": 17' in system
    assert '"resource_pressure": 0.8' in system
    assert "Inspect local state" in system
    assert "Earlier hypothesis" in system
    assert "not verified facts" in system
    assert "UNBOUNDED_GRAPH_MUST_NOT_ENTER_PROMPT" not in system
