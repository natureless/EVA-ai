"""Policy engine observability endpoints.

GET  /api/policy/state        — current state machine status + token counts
GET  /api/policy/state?detail=true — full state with token list and history
POST /api/policy/transition   — manual state transition (user confirmation required)
"""

from fastapi import APIRouter, Request, HTTPException

router = APIRouter()


@router.get("/api/policy/state")
async def get_policy_state(request: Request, detail: bool = False):
    container = request.app.state.container
    policy_engine = container.get("policy_engine")
    if policy_engine is None:
        raise HTTPException(status_code=503, detail="policy engine not available")

    state = policy_engine.get_state()
    if not detail:
        sm = state["state_machine"]
        tk = state["tokens"]
        return {
            "state": sm["current"],
            "state_since_seconds": sm.get("elapsed_seconds", 0),
            "history_size": sm.get("history_size", 0),
            "active_tokens": tk.get("active_tokens", 0),
        }

    return state


@router.post("/api/policy/transition")
async def trigger_state_transition(request: Request):
    """Manually trigger a state machine transition.

    Request body: {"trigger": "manual_intervention"}
    Used to recover from Quarantine or to grant Supervised mode.
    """
    container = request.app.state.container
    policy_engine = container.get("policy_engine")
    if policy_engine is None:
        raise HTTPException(status_code=503, detail="policy engine not available")

    # FastAPI body is read from request directly since we don't want pydantic model overhead
    import json
    try:
        body = await request.body()
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    trigger = payload.get("trigger", "")
    if not trigger:
        raise HTTPException(status_code=400, detail="missing 'trigger' field")

    allowed_triggers = {"manual_intervention", "autonomy_granted", "user_revoke"}
    if trigger not in allowed_triggers:
        raise HTTPException(
            status_code=400,
            detail=f"trigger must be one of: {', '.join(sorted(allowed_triggers))}",
        )

    decision = policy_engine.transition(trigger)
    container["system_state"]["policy_state"] = policy_engine.get_state()

    return {
        "ok": decision.verdict == "allow",
        "verdict": decision.verdict,
        "reason": decision.reason,
    }
