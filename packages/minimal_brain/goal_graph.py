"""Bounded, read-only projection of persisted goals and their evidence references."""

from datetime import datetime, timezone
import json


def project_goals(records, *, total, offset, limit, query):
    nodes, edges = [], []
    episode_nodes = {}

    def node(tier, key, label, kind, record, timestamp=None, notice=""):
        item = {
            "id": f"{tier}:{key}",
            "record_id": key,
            "tier": tier,
            "label": label,
            "kind": kind,
            "timestamp": timestamp,
            "source": "business_goal_store",
            "content": json.dumps(record, ensure_ascii=False, indent=2),
            "record": record,
            "notice": notice,
            "provenance": {"source_event_id": record.get("source_event_id")},
        }
        nodes.append(item)
        return item["id"]

    def edge(source, target, relation):
        edges.append(
            {
                "source": source,
                "target": target,
                "kind": "goal_evidence",
                "relation": relation,
            }
        )

    for item in records:
        goal, receipt, runs = item["goal"], item["receipt"], item["runs"]
        goal_id = node(
            "G",
            goal["goal_id"],
            goal["description"],
            "business_goal",
            goal,
            goal["updated_at"],
            "状态为已存快照；浏览不对账、不推进过期状态。完成仅表示指定成功条件曾通过检查。",
        )
        nodes[-1]["status"] = goal["status"]
        nodes[-1]["verification_count"] = item["verification_count"]
        nodes[-1]["history_truncated"] = item["verification_count"] > len(runs)
        if receipt:
            receipt_id = node(
                "R",
                goal["goal_id"],
                "请求回执 · " + receipt["terminal_state"],
                "processing_receipt",
                receipt,
                notice="请求处理成功不等于业务目标完成；回执未记录独立时间戳。",
            )
            nodes[-1]["status"] = receipt["terminal_state"]
            edge(goal_id, receipt_id, "processing_receipt")
        for run in runs:
            run_id = node(
                "V",
                run["run_id"],
                f"文件检查 v{run['committed_goal_version']} · {run['outcome']}",
                "file_verification",
                run,
                run["observed_at"],
                "仅反映采样时刻指定文件的内容检查，不证明当前文件仍一致，也不证明代码质量。",
            )
            nodes[-1]["status"] = run["outcome"]
            edge(goal_id, run_id, "verification_run")

        reference = item.get("episode_reference", {})
        if reference.get("status") == "available":
            episode = reference["episode"]
            episode_id = episode_nodes.get(episode["episode_id"])
            if episode_id is None:
                episode_id = node(
                    "E",
                    episode["episode_id"],
                    "执行记录 · " + episode["result_status"],
                    "processing_episode",
                    episode,
                    episode["started_at"],
                    "同一来源事件的已存 Episode 引用；执行结果不等于业务目标完成，查看不会重放动作。",
                )
                episode_nodes[episode["episode_id"]] = episode_id
                nodes[-1]["source"] = "episode_store"
                nodes[-1]["provenance"] = {"source_event_id": episode["event_id"]}
                nodes[-1]["status"] = "episode_" + episode["result_status"]
            edge(goal_id, episode_id, "source_event_episode")

    return {
        "nodes": nodes,
        "edges": edges,
        "query": query,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            tier: {"available": True, "total": sum(n["tier"] == tier for n in nodes)}
            for tier in ("G", "R", "V", "E")
        },
        "scope": {
            "matched_nodes": len(nodes),
            "matched_goals": total,
            "goal_offset": offset,
            "goal_limit": limit,
            "history_limit": 5,
            "history_truncated": any(n.get("history_truncated") for n in nodes),
            "nodes_truncated": offset > 0 or offset + len(records) < total,
            "read_only": True,
            "episode_references_unavailable": sum(
                item.get("episode_reference", {}).get("status") == "unavailable"
                for item in records
            ),
        },
        "next_offset": offset + limit if offset + len(records) < total else None,
    }
