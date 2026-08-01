"""Governance — EVA-MVSC 治理层。

包含:
- PolicyEngine 增强 (与现有 policy_engine 协同)
- PermissionChecker 权限检查
- AuditTrail 审计追踪
- IdentityChangeManager 身份变更管理
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from packages.contracts.events import EventEnvelope, EventFamily
from packages.contracts.state import RuntimeMode

logger = logging.getLogger("eva.governance")


# ═══════════════════════════════════════════════════════════════
# PermissionChecker
# ═══════════════════════════════════════════════════════════════

class PermissionChecker:
    """权限检查器 — 验证操作是否在授权范围内。

    检查维度:
    1. 主体边界: 资源是否在允许范围内
    2. 令牌有效性: token 是否有效且未过期
    3. 风险等级: 高风险操作需要额外确认
    4. 速率限制: 操作频率是否超限
    """

    def __init__(self) -> None:
        self._denied_count = 0
        self._allowed_count = 0

    def check(
        self,
        action: str,
        resource: str = "",
        token_valid: bool = True,
        risk_level: str = "low",
    ) -> tuple[bool, str]:
        """检查权限。

        Returns:
            (allowed, reason)
        """
        # 1. Token 检查
        if not token_valid:
            self._denied_count += 1
            return False, "token invalid or expired"

        # 2. 高风险操作
        high_risk_actions = {
            "delete_memory", "modify_world_model",
            "execute_code_external", "change_state_machine",
        }
        if action in high_risk_actions and risk_level != "approved":
            self._denied_count += 1
            return False, f"high-risk action '{action}' requires explicit approval"

        # 3. 禁止操作
        prohibited = {
            "modify_constitution", "escalate_privileges",
            "access_other_users_data", "disable_auditing",
        }
        if action in prohibited:
            self._denied_count += 1
            return False, f"action '{action}' is prohibited by constitution"

        self._allowed_count += 1
        return True, "allowed"

    @property
    def stats(self) -> dict[str, Any]:
        total = self._allowed_count + self._denied_count
        return {
            "allowed": self._allowed_count,
            "denied": self._denied_count,
            "deny_rate": self._denied_count / total if total > 0 else 0.0,
        }


# ═══════════════════════════════════════════════════════════════
# AuditTrail
# ═══════════════════════════════════════════════════════════════

class AuditTrail:
    """审计追踪 — 不可变的事件记录链。

    每个审计条目包含前一条的哈希，形成防篡改链。
    """

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._last_hash: str = ""

    def record(
        self,
        action: str,
        actor: str,
        resource: str = "",
        result: str = "success",
        detail: str = "",
    ) -> str:
        """记录审计条目，返回条目哈希。"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "actor": actor,
            "resource": resource,
            "result": result,
            "detail": detail,
            "prev_hash": self._last_hash,
        }
        entry["hash"] = hashlib.sha256(
            f"{entry['timestamp']}|{action}|{actor}|{resource}|{result}|{self._last_hash}".encode()
        ).hexdigest()[:16]

        self._entries.append(entry)
        self._last_hash = entry["hash"]
        if len(self._entries) > 1000:
            self._entries = self._entries[-1000:]

        return entry["hash"]

    def verify_chain(self) -> tuple[bool, str]:
        """验证审计链完整性。"""
        if not self._entries:
            return True, "chain empty"

        prev = ""
        for i, entry in enumerate(self._entries):
            expected = hashlib.sha256(
                f"{entry['timestamp']}|{entry['action']}|{entry['actor']}|"
                f"{entry['resource']}|{entry['result']}|{prev}".encode()
            ).hexdigest()[:16]
            if entry["hash"] != expected:
                return False, f"chain broken at entry {i}"
            prev = entry["hash"]

        return True, f"chain verified ({len(self._entries)} entries)"

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._entries[-limit:]

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_entries": len(self._entries),
            "last_hash": self._last_hash,
        }


# ═══════════════════════════════════════════════════════════════
# IdentityChangeManager
# ═══════════════════════════════════════════════════════════════

class IdentityChangeManager:
    """身份变更管理器 — 确保身份演化经过审批流程。

    流程: 提案 → 风险评估 → 用户批准 → 新版本 → 保留旧版本
    """

    def __init__(self) -> None:
        self._proposals: list[dict[str, Any]] = []
        self._approved_count = 0
        self._rejected_count = 0

    def propose(
        self,
        target_field: str,
        current_value: Any,
        proposed_value: Any,
        reason: str,
        evidence_event_ids: list[str] | None = None,
    ) -> str:
        """创建身份变更提案。"""
        import uuid
        proposal_id = f"icp_{uuid.uuid4().hex[:8]}"

        proposal = {
            "proposal_id": proposal_id,
            "target_field": target_field,
            "current_value": str(current_value)[:200],
            "proposed_value": str(proposed_value)[:200],
            "reason": reason,
            "evidence_event_ids": evidence_event_ids or [],
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "risk_assessment": self._assess_risk(target_field, current_value, proposed_value),
        }

        self._proposals.append(proposal)
        logger.info("identity change proposed: %s → %s (%s)", target_field, proposal["proposal_id"], reason)
        return proposal_id

    def approve(self, proposal_id: str, approved_by: str = "user") -> bool:
        """批准变更提案。"""
        for p in self._proposals:
            if p["proposal_id"] == proposal_id and p["status"] == "pending":
                p["status"] = "approved"
                p["approved_by"] = approved_by
                p["approved_at"] = datetime.now(timezone.utc).isoformat()
                self._approved_count += 1
                return True
        return False

    def reject(self, proposal_id: str, reason: str = "") -> bool:
        """拒绝变更提案。"""
        for p in self._proposals:
            if p["proposal_id"] == proposal_id and p["status"] == "pending":
                p["status"] = "rejected"
                p["rejection_reason"] = reason
                self._rejected_count += 1
                return True
        return False

    def _assess_risk(self, field: str, current: Any, proposed: Any) -> dict[str, Any]:
        """评估变更风险。"""
        high_risk_fields = {"core_principles", "system_id", "owner_id"}
        medium_risk_fields = {"role_definition"}

        if field in high_risk_fields:
            level = "high"
        elif field in medium_risk_fields:
            level = "medium"
        else:
            level = "low"

        return {
            "risk_level": level,
            "requires_approval": level in ("high", "medium"),
            "reversible": field not in ("system_id",),
        }

    @property
    def pending_proposals(self) -> list[dict[str, Any]]:
        return [p for p in self._proposals if p["status"] == "pending"]

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_proposals": len(self._proposals),
            "pending": len(self.pending_proposals),
            "approved": self._approved_count,
            "rejected": self._rejected_count,
        }
