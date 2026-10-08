"""Read-only graph of an actual invocation and its historical input references."""

from datetime import datetime, timezone
import json

from memory.context_evidence import ActionContextEvidence, context_hash


def project_action_context(view):
    inputs = ActionContextEvidence.model_validate(view["inputs"])
    if inputs.world is None or inputs.memory is None:
        raise ValueError("actual input references required")
    intent = view["intent"]
    observation = view["observation"]
    if (
        observation["action_id"] != intent["action_id"]
        or observation["source_event_id"] != view["event_id"]
        or observation["state_version"] != intent["state_version"]
        or observation["request_hash"] != intent["request_hash"]
    ):
        raise ValueError("action context identity mismatch")
    binding = {
        "task_id": view["task_id"],
        "event_id": view["event_id"],
        **{key: intent[key] for key in ("action_id", "state_version", "request_hash")},
    }
    nodes, edges = [], []

    def node(tier, identity, label, kind, record, timestamp=None):
        identifier = f"{tier}:{identity}"
        nodes.append(
            {
                "id": identifier,
                "record_id": identity,
                "tier": tier,
                "label": label,
                "kind": kind,
                "record": record,
                "content": json.dumps(record, ensure_ascii=False, indent=2),
                "timestamp": timestamp,
                "source": "durable_request_store",
                "provenance": {"source_event_id": view["event_id"]},
                "notice": "执行时保存的输入引用；不提供历史正文，不证明内容属实，也不表示当前记忆仍一致。",
            }
        )
        return identifier

    observed = {
        key: observation[key]
        for key in (
            "status",
            "started_at",
            "finished_at",
            "sealed_at",
            "observation_kind",
            "returned_ok",
        )
    }
    action = node(
        "A",
        intent["action_id"],
        "Agent 行动 · " + view["selected_agent"],
        "agent_input_intent",
        {
            **binding,
            "parent_action_id": view["parent_action_id"],
            "selected_agent": view["selected_agent"],
            "context_hash": inputs.context_hash,
            "task_hash": inputs.task_hash,
            "observation": observed,
        },
        observation["started_at"],
    )
    nodes[-1]["status"] = "action_" + observation["status"]

    def reference(label, kind, record, relation):
        target = node("I", context_hash(record), label, kind, record)
        edges.append(
            {
                "source": action,
                "target": target,
                "kind": "action_input",
                "relation": relation,
            }
        )

    reference(
        "世界上下文 · 历史引用",
        "historical_world_view",
        inputs.world.model_dump(mode="json"),
        "used_world_context",
    )
    memory_view = inputs.memory.model_dump(mode="json")
    reference(
        "记忆检索视图 · 历史引用",
        "historical_memory_view",
        memory_view,
        "used_memory_view",
    )
    for item in inputs.memory_items:
        reference(
            f"{item.tier} 输入记忆 · {item.memory_id[:80]}",
            "historical_memory_input",
            {**item.model_dump(mode="json"), "memory_view": memory_view},
            "used_memory_input",
        )
    return {
        "schema_version": 1,
        "binding": binding,
        "nodes": nodes,
        "edges": edges,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            tier: {"available": True, "total": sum(n["tier"] == tier for n in nodes)}
            for tier in ("A", "I")
        },
        "scope": {
            "read_only": True,
            "historical_references_only": True,
            "node_limit": 13,
            "edge_limit": 12,
            "memory_input_limit": 10,
            "external_actions_replayed": False,
        },
    }
