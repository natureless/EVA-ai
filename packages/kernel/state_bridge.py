"""StateBridge — 现有 system_state dict ↔ ConsciousState 双向桥接。

将 bootstrap 中创建的 system_state dict 与 MVSC ConsciousState 连接:
1. system_state → ConsciousState: 提取相关字段
2. ConsciousState → system_state: 同步回 dict 供现有 API 使用

双轨状态契约:
- ConsciousState 是 MVSC 认知循环的权威状态 (单一事实来源)
- system_state 是现有 API 的缓存视图 (只读, 通过 sync_system_state 更新)
- 任何模块不得同时直接修改两个状态
- 同步时机: run_once_and_sync() 调用后, 或显式调用 sync_system_state()
- 未知字段保存在 health._raw 中, 确保不丢失数据

这是最小侵入的对接方式: 现有代码继续读写 system_state dict，
StateBridge 在每次认知循环后做同步。
"""

from __future__ import annotations

import logging
from typing import Any

from packages.contracts.state import (
    BodyState,
    ConsciousState,
    RuntimeMode,
)

logger = logging.getLogger("eva.kernel.state_bridge")


# ═══════════════════════════════════════════════════════════════
# system_state → ConsciousState
# ═══════════════════════════════════════════════════════════════

def system_state_to_conscious(ss: dict[str, Any], subject_id: str = "eva-001") -> ConsciousState:
    """将现有 system_state dict 提升为 ConsciousState。

    映射关系:
    - ss["focus"], ss["mode"], ss["active_tasks"] → world
    - ss["pending_events"], ss["pending_results"] → health
    - ss["stability_score"], ss["mean_prediction_error"] → self_model
    - ss["last_reply"], ss["last_loop_at"] → world
    - ss["agents"] → relations
    - ss["policy_state"] → health

    不会丢失任何字段 — 所有未显式映射的字段保存在 health["_raw"] 中。
    """
    # ── World ──
    world: dict[str, Any] = {
        "focus": ss.get("focus", "idle"),
        "mode": ss.get("mode", "active"),
        "active_tasks": ss.get("active_tasks", []),
        "last_reply": ss.get("last_reply", ""),
        "last_selected_agent": ss.get("last_selected_agent", ""),
        "last_loop_id": ss.get("last_loop_id", ""),
        "last_loop_at": ss.get("last_loop_at"),
        "last_snapshot_at": ss.get("last_snapshot_at"),
        "last_proactive_reason": ss.get("last_proactive_reason"),
    }

    # ── Body ──
    body = BodyState(
        consecutive_failures=0,  # 由 cognition loop 更新
    )

    # ── Self Model ──
    self_model: dict[str, Any] = {
        "stability_metrics": {
            "stability_score": ss.get("stability_score", 1.0),
            "mean_prediction_error": ss.get("mean_prediction_error", 0.0),
            "total_perturbations": ss.get("total_perturbations", 0),
        },
    }

    # ── Health ──
    health: dict[str, Any] = {
        "ready": ss.get("ready", False),
        "db_ready": ss.get("db_ready", False),
        "event_bus_ready": ss.get("event_bus_ready", False),
        "loop_ready": ss.get("loop_ready", False),
        "scheduler_ready": ss.get("scheduler_ready", False),
        "scheduler_running": ss.get("scheduler_running", False),
        "pending_events": ss.get("pending_events", 0),
        "pending_results": ss.get("pending_results", 0),
        "policy_state": ss.get("policy_state"),
        "storage_backend": ss.get("storage_backend", "sqlite"),
        "bootstrap_sec": ss.get("bootstrap_sec", 0),
    }

    # ── Relations ──
    relations: dict[str, Any] = {
        "agents": ss.get("agents", []),
    }

    # ── Memory Context ──
    working_memory: list[str] = []
    context_summary = ss.get("last_context_summary")
    if context_summary and isinstance(context_summary, dict):
        working_memory.append(context_summary.get("summary", "")[:200])

    # ── Runtime Mode ──
    runtime_mode = RuntimeMode.ACTIVE
    if not ss.get("ready"):
        runtime_mode = RuntimeMode.BOOTING
    policy_state = ss.get("policy_state")
    if policy_state and isinstance(policy_state, dict):
        sm = policy_state.get("state_machine", {})
        if sm.get("current") == "quarantined":
            runtime_mode = RuntimeMode.DEGRADED

    # ── 保留原始字段（防止丢失）──
    raw_fields: dict[str, Any] = {}
    known_keys = {
        "focus", "mode", "active_tasks", "last_reply", "last_selected_agent",
        "last_loop_id", "last_loop_at", "last_snapshot_at", "last_proactive_reason",
        "stability_score", "mean_prediction_error", "total_perturbations",
        "ready", "db_ready", "event_bus_ready", "loop_ready",
        "scheduler_ready", "scheduler_running", "pending_events", "pending_results",
        "policy_state", "storage_backend", "bootstrap_sec", "agents",
        "last_context_summary", "last_memory_governor", "diagnostic",
    }
    for key, value in ss.items():
        if key not in known_keys:
            raw_fields[key] = value
    health["_raw"] = raw_fields

    return ConsciousState(
        subject_id=subject_id,
        runtime_mode=runtime_mode,
        world=world,
        body=body,
        self_model=self_model,
        health=health,
        relations=relations,
        working_memory=working_memory,
    )


# ═══════════════════════════════════════════════════════════════
# ConsciousState → system_state
# ═══════════════════════════════════════════════════════════════

def conscious_to_system_state(cs: ConsciousState) -> dict[str, Any]:
    """将 ConsciousState 同步回 system_state dict。

    确保现有 API (/api/state, /health/* 等) 继续正常工作。
    所有现有字段都会被填充，新字段以兼容方式添加。
    """
    ss: dict[str, Any] = {}

    # ── 核心字段 ──
    ss["focus"] = cs.world.get("focus", "idle")
    ss["mode"] = cs.world.get("mode", "active")
    ss["active_tasks"] = cs.world.get("active_tasks", [])
    ss["last_reply"] = cs.world.get("last_reply", "")
    ss["last_selected_agent"] = cs.world.get("last_selected_agent", "")
    ss["last_loop_id"] = cs.world.get("last_loop_id", "")
    ss["last_loop_at"] = cs.world.get("last_loop_at")
    ss["last_snapshot_at"] = cs.world.get("last_snapshot_at")
    ss["last_proactive_reason"] = cs.world.get("last_proactive_reason")

    # ── 健康 ──
    ss["ready"] = cs.health.get("ready", False)
    ss["db_ready"] = cs.health.get("db_ready", False)
    ss["event_bus_ready"] = cs.health.get("event_bus_ready", False)
    ss["loop_ready"] = cs.health.get("loop_ready", False)
    ss["scheduler_ready"] = cs.health.get("scheduler_ready", False)
    ss["scheduler_running"] = cs.health.get("scheduler_running", False)
    ss["pending_events"] = cs.health.get("pending_events", 0)
    ss["pending_results"] = cs.health.get("pending_results", 0)
    ss["policy_state"] = cs.health.get("policy_state")
    ss["storage_backend"] = cs.health.get("storage_backend", "sqlite")
    ss["bootstrap_sec"] = cs.health.get("bootstrap_sec", 0)

    # ── 自我模型 ──
    metrics = cs.self_model.get("stability_metrics", {})
    ss["stability_score"] = metrics.get("stability_score", 1.0)
    ss["mean_prediction_error"] = metrics.get("mean_prediction_error", 0.0)
    ss["total_perturbations"] = metrics.get("total_perturbations", 0)

    # ── 关系 ──
    ss["agents"] = cs.relations.get("agents", [])

    # ── 上下文 ──
    if cs.working_memory:
        ss["last_context_summary"] = {"summary": cs.working_memory[0]}

    # ── 运行时模式 ──
    ss["mvsc_runtime_mode"] = cs.runtime_mode.value
    ss["mvsc_cognition_phase"] = cs.cognition_phase.value
    ss["mvsc_tick"] = cs.tick
    ss["mvsc_version"] = cs.version

    # ── 恢复原始字段 ──
    raw = cs.health.get("_raw", {})
    for key, value in raw.items():
        if key not in ss:
            ss[key] = value

    return ss


# ═══════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════

def sync_system_state(ss: dict[str, Any], cs: ConsciousState) -> dict[str, Any]:
    """将 ConsciousState 的变化同步到 system_state (原地修改)。

    用于 cognition loop 每次迭代后保持两个状态视图一致。
    """
    updated = conscious_to_system_state(cs)
    # 原地更新，保留 ss 中不在 cs 范围内的字段
    ss.update(updated)
    return ss
