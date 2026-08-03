"""Comprehensive verification suite for EVA-MVSC framework integration.

Validates:
1. All imports work
2. Legacy ↔ new event compatibility
3. RuntimeMode coverage
4. ContentCandidate priority formula
5. ViabilityBounds detection
6. SelfModel 6 sub-models + legacy compat
7. CapabilityModel calibrated confidence
8. AgencyModel self-attribution
9. NarrativeModel evidence constraint
10. EventStore: append-only, sequence, replay, causal chain, duplicate detection, integrity, deterministic hash
11. PipelineCognitionLoop full cycle
12. Ablation feature flags
13. ConsciousState.apply() integrity
14. EventFamily completeness
"""

import asyncio
import os
import sys
import tempfile

import pytest
from event.event_schema import Event as LegacyEvent

sys.path.insert(0, ".")

pass_count = 0
fail_count = 0


def check(name: str) -> None:
    global pass_count
    pass_count += 1
    print(f"[PASS] {pass_count:02d}. {name}")


def fail(name: str, error: str) -> None:
    global fail_count
    fail_count += 1
    print(f"[FAIL] {name}: {error}")


# ─── 1. All imports ───
try:
    from packages.contracts.events import EventEnvelope, EventFamily, from_legacy_event, LEGACY_EVENT_MAP
    from packages.contracts.state import (
        ConsciousState, BodyState, ViabilityBounds, ContentCandidate,
        RuntimeMode,
    )
    from packages.contracts.protocols import (
        ContentEngineProtocol, AttentionProtocol, WorkspaceProtocol,
        MetacognitionProtocol,
    )
    from packages.models.self_model import (
        SelfModel, AgencyModel, NarrativeModel,
        ActionReceipt, NarrativeNode,
    )
    from packages.kernel.event_store import EventStore, DuplicateEventError
    from packages.cognition.loop import (
        ContentEngine, Attention, Workspace, Metacognition, PipelineCognitionLoop,
    )
    from packages.mvsc_lab.ablations import (
        AblationConfig,
    )
    check("All imports clean")
except Exception as e:
    fail("All imports", str(e))
    sys.exit(1)

# ─── 2. Legacy Event compatibility ───
legacy = LegacyEvent(type="user_message", source="user", payload={"text": "hello"})
envelope = from_legacy_event(legacy)
assert envelope.event_id == legacy.id, f"{envelope.event_id} != {legacy.id}"
assert envelope.event_type == "perception.user_message_received"
assert envelope.correlation_id == legacy.correlation_id or envelope.correlation_id.startswith("corr_")

for t in ("user_message", "maintenance", "reminder_trigger", "system_tick"):
    assert t in LEGACY_EVENT_MAP, f"Missing mapping: {t}"
check("Legacy Event → EventEnvelope conversion + all types mapped")

# ─── 3. RuntimeMode 8 states ───
new_states = {s.value for s in RuntimeMode}
assert "active" in new_states
assert "degraded" in new_states
assert "recovering" in new_states
assert "reflecting" in new_states
assert "consolidating" in new_states
check("RuntimeMode 8 states cover all required modes")

# ─── 4. ContentCandidate priority formula ───
c = ContentCandidate(
    content_type="test", summary="test", source_module="test",
    salience=0.8, goal_relevance=0.7, viability_risk=0.9,
    novelty=0.5, uncertainty=0.3, deadline_pressure=0.1,
    expected_impact=0.6, inhibition=0.2,
)
p = c.priority
expected = (
    0.20 * 0.8 + 0.20 * 0.7 + 0.25 * 0.9
    + 0.10 * 0.5 + 0.10 * 0.3 + 0.05 * 0.1
    + 0.10 * 0.6 - 0.10 * 0.2
)
assert abs(p - expected) < 0.001, f"{p} != {expected}"
check(f"ContentCandidate priority = {p:.3f} (formula verified)")

# ─── 5. ViabilityBounds detection ───
body = BodyState(memory_usage=95.0, error_rate=0.5, consecutive_failures=6)
violations = ViabilityBounds().check(body)
assert len(violations) == 3
check(f"ViabilityBounds detects {len(violations)} violations correctly")

# ─── 6. SelfModel 6 sub-models + legacy compat ───
sm = SelfModel()
assert sm.identity.system_id == "eva-001"
assert sm.identity.identity_version == "1.0.0"
assert len(sm.identity.core_principles) == 4
assert len(sm.boundary.forbidden_paths) >= 3

legacy_sm = SelfModel.from_legacy({"system_id": "eva-legacy", "stability_metrics": {"score": 0.8}})
assert legacy_sm.identity.system_id == "eva-legacy"
check("SelfModel 6 sub-models + legacy compat")

# ─── 7. CapabilityModel calibrated confidence ───
sm.capability.register_capability("test_cap", "Test", "Test capability")
sm.capability.record_success("test_cap")
sm.capability.record_success("test_cap")
sm.capability.record_failure("test_cap", "timeout")
conf = sm.capability.capabilities["test_cap"].confidence
assert abs(conf - 0.50) < 0.001, f"Expected ~0.50, got {conf}"
check(f"CapabilityModel confidence calibrated: {conf:.2f}")

# ─── 8. AgencyModel self-attribution ───
receipt = ActionReceipt(intent_id="int-1", plan_id="plan-1", actor_id="self")
agency = AgencyModel()
result = agency.attribute(receipt, {"has_active_intent": True, "token_id": "tok-1"})
assert result == "self"
agency.record_action(receipt)
assert agency.self_initiated_count == 1
check("AgencyModel self-attribution + action recording")

# ─── 9. NarrativeModel evidence constraint ───
try:
    NarrativeModel().add_node(NarrativeNode(summary="test", evidence_event_ids=[]))
    assert False, "Should have raised ValueError"
except ValueError as e:
    assert "evidence_event_ids" in str(e)
check("NarrativeModel enforces evidence constraint")

# ─── 10. EventStore full verification ───
db_path = os.path.join(tempfile.gettempdir(), "verify_store.db")
if os.path.exists(db_path):
    os.unlink(db_path)
store = EventStore(db_path)

e1 = EventEnvelope(event_type="test.one", source="test", payload={"n": 1})
e2 = EventEnvelope(event_type="test.two", source="test", payload={"n": 2}, causation_id=e1.event_id)

s1 = store.append(e1)
s2 = store.append(e2)
assert s2 == s1 + 1, f"Sequence gap: {s1} -> {s2}"
assert e1.sequence == s1
assert e2.sequence == s2

replayed = store.replay()
assert len(replayed) == 2
assert replayed[0].sequence == s1
assert replayed[1].sequence == s2

chain = store.get_causal_chain(e2.event_id)
assert len(chain) == 2
assert chain[1].event_id == e1.event_id

try:
    store.append(e1)
    assert False, "Should have raised DuplicateEventError"
except DuplicateEventError:
    pass

integrity = store.verify_integrity()
assert integrity["ok"] is True

h1 = store.compute_state_hash()
h2 = store.compute_state_hash()
assert h1 == h2, "Hash not deterministic!"

store.close()
os.unlink(db_path)
check("EventStore: append-only, sequence, replay, causal chain, duplicate, integrity, deterministic hash")

# ─── 11. Full PipelineCognitionLoop cycle ───
@pytest.mark.asyncio
async def test_pipeline():
    loop = PipelineCognitionLoop(feature_flags={
        "recurrent_content": True, "global_workspace": True,
        "self_model": True, "value_model": True,
        "episodic_memory": True, "narrative_identity": True,
        "metacognition": True,
    })
    event = EventEnvelope(event_type="perception.user_message_received", source="user", payload={"text": "hello"})
    state = await loop.run_once(event)
    assert state.tick == 1
    assert state.version == 1
    assert len(state.active_contents) >= 1
    assert state.integrity_hash != ""
    assert len(loop.phase_timings) > 0
    return state

state = asyncio.run(test_pipeline())
check(f"PipelineCognitionLoop: tick={state.tick}, {len(state.active_contents)} candidates, hash={state.integrity_hash}")

# ─── 12. Ablation feature flags ───
cfg = AblationConfig()
assert cfg.global_workspace is True
assert cfg.self_model is True

no_ws = cfg.disable("global_workspace")
assert no_ws.global_workspace is False
assert no_ws.self_model is True

minimal = cfg.enable_only("recurrent_content")
assert minimal.recurrent_content is True
assert minimal.global_workspace is False

@pytest.mark.asyncio
async def test_ablated_pipeline():
    loop = PipelineCognitionLoop(feature_flags=no_ws.to_dict())
    event = EventEnvelope(event_type="test", source="test", payload={})
    state = await loop.run_once(event)
    assert len(state.workspace) == 0  # workspace disabled → no broadcast
    return state

state2 = asyncio.run(test_ablated_pipeline())
check(f"Ablation: workspace disabled → {len(state2.workspace)} broadcast items (expected 0)")

# ─── 13. ConsciousState.apply() integrity ───
s1 = ConsciousState(subject_id="test")
s2 = s1.apply(world_delta={"focus": "new_focus"}, body_delta={"cpu_load": 50.0})
assert s2.version == s1.version + 1
assert s2.tick == s1.tick + 1
assert s2.world["focus"] == "new_focus"
assert s2.body.cpu_load == 50.0
assert s2.integrity_hash != s1.integrity_hash
check(f"ConsciousState.apply(): v{s2.version}, hash changed correctly")

# ─── 14. EventFamily completeness ───
families = [
    "RUNTIME", "PERCEPTION", "WORLD", "BODY", "ATTENTION",
    "WORKSPACE", "SELF", "GOAL", "PLAN", "ACTION",
    "VERIFICATION", "MEMORY", "IDENTITY", "LIFECYCLE",
    "SECURITY", "EXPERIMENT",
]
for fam in families:
    assert hasattr(EventFamily, fam), f"Missing EventFamily.{fam}"
check(f"EventFamily: all {len(families)} families present")

# ─── 15. Protocol compliance check ───
# Verify ContentEngine conforms to ContentEngineProtocol
assert isinstance(ContentEngine(), ContentEngineProtocol)
# Verify Attention conforms to AttentionProtocol
assert isinstance(Attention(), AttentionProtocol)
# Verify Workspace conforms to WorkspaceProtocol
assert isinstance(Workspace(), WorkspaceProtocol)
# Verify Metacognition conforms to MetacognitionProtocol
assert isinstance(Metacognition(), MetacognitionProtocol)
check("All implementations conform to their protocols")

# ─── 16. SelfModel → ConsciousState integration ───
sm_dict = SelfModel().to_dict()
cs = ConsciousState(self_model=sm_dict)
assert "identity" in cs.self_model
assert "agency" in cs.self_model
assert "capability" in cs.self_model
assert "narrative" in cs.self_model
check("SelfModel serializes into ConsciousState.self_model correctly")

print()
print("=" * 60)
print(f"ALL {pass_count} VERIFICATION CHECKS PASSED, {fail_count} FAILED")
print("=" * 60)
