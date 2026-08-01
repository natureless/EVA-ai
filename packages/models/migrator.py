"""SelfModelMigrator — 将旧 self_model.json 迁移为 MVSC SelfModel 6子模型。

迁移路径:
1. 读取 data/self_model.json (现有格式)
2. 创建 SelfModel 6子模型
3. 映射字段:
   - identity → IdentityModel
   - capabilities[] → CapabilityModel
   - constraints[] → BoundaryModel
   - state_history[] → 迁移到 agency + narrative
   - perturbations[], prediction_errors[] → stability_metrics
4. 生成新格式文件 data/self_model_v2.json (保留旧文件)
5. 后续 bootstrap 可选用新格式

关键原则:
- 旧文件不被修改
- 迁移是幂等的
- 缺失字段使用合理默认值
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packages.models.self_model import (
    ActionReceipt,
    IdentityModel,
    NarrativeNode,
    SelfModel,
)

logger = logging.getLogger("eva.models.migrator")


# ═══════════════════════════════════════════════════════════════
# 迁移器
# ═══════════════════════════════════════════════════════════════

class SelfModelMigrator:
    """将旧的 self_model.json 迁移为 MVSC SelfModel 6子模型。

    Usage::

        migrator = SelfModelMigrator()
        new_model = migrator.migrate_file("data/self_model.json")
        migrator.save(new_model, "data/self_model_v2.json")
    """

    def __init__(self, subject_id: str = "eva-001") -> None:
        self.subject_id = subject_id

    def migrate_file(self, path: str | Path) -> SelfModel:
        """从文件路径迁移。"""
        path = Path(path)
        if not path.exists():
            logger.warning("self_model file not found at %s — creating default", path)
            return SelfModel()

        with path.open("r", encoding="utf-8") as fh:
            legacy = json.load(fh)

        return self.migrate_dict(legacy)

    def migrate_dict(self, legacy: dict[str, Any]) -> SelfModel:
        """从旧 dict 迁移到新 SelfModel。"""
        sm = SelfModel()

        # ── 1. IdentityModel ──
        sm.identity = self._migrate_identity(legacy)

        # ── 2. CapabilityModel ──
        sm.capability = self._migrate_capabilities(legacy)

        # ── 3. BoundaryModel ──
        sm.boundary = self._migrate_boundary(legacy)

        # ── 4. AgencyModel ──
        sm.agency = self._migrate_agency(legacy)

        # ── 5. NarrativeModel ──
        sm.narrative = self._migrate_narrative(legacy)

        # ── 6. Stability metrics ──
        sm.stability_metrics = legacy.get("stability_metrics", {
            "stability_score": 1.0,
            "mean_prediction_error": 0.0,
            "total_perturbations": 0,
        })

        # ── 标记迁移来源 ──
        sm.identity.change_history.append({
            "action": "migrated_from_legacy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "legacy_version": legacy.get("_version", 1),
            "legacy_identity": legacy.get("identity", "unknown"),
        })

        return sm

    # ── 子迁移方法 ─────────────────────────────────────────

    def _migrate_identity(self, legacy: dict[str, Any]) -> IdentityModel:
        """迁移身份信息。"""
        return IdentityModel(
            system_id=self.subject_id,
            identity_version=f"2.0.0-migrated-{legacy.get('_version', 1)}",
            role_definition=str(legacy.get("identity", "EVA v0.1")),
            core_principles=[
                "do not fabricate memories",
                "do not overclaim certainty",
                "do not violate user boundaries",
                "maintain audit trail for all actions",
            ],
            creation_time=datetime.now(timezone.utc),
        )

    def _migrate_capabilities(self, legacy: dict[str, Any]) -> Any:
        """将 capabilities 列表迁移为 CapabilityModel。"""
        from packages.models.self_model import CapabilityModel

        model = CapabilityModel()
        caps = legacy.get("capabilities", [])

        capability_descriptions = {
            "chat": "Handle general conversational tasks with LLM + context + tools",
            "docs_summary": "Summarize and analyze documents",
            "memory_write": "Write episodic memories to persistent storage",
            "trace_record": "Record execution traces for audit and debugging",
            "search": "Full-text search across local files",
            "code_analysis": "Analyze and summarize code files",
        }

        for cap_name in caps:
            desc = capability_descriptions.get(cap_name, f"Legacy capability: {cap_name}")
            model.register_capability(
                capability_id=cap_name,
                name=cap_name.replace("_", " ").title(),
                description=desc,
            )
            # 已存在的旧能力给予高置信度
            if cap_name in model.capabilities:
                model.capabilities[cap_name].confidence = 0.8

        return model

    def _migrate_boundary(self, legacy: dict[str, Any]) -> Any:
        """将 constraints 列表迁移为 BoundaryModel。"""
        from packages.models.self_model import BoundaryModel

        model = BoundaryModel()
        constraints = legacy.get("constraints", [])

        # 将旧约束映射到边界
        for c in constraints:
            model.permission_scope.add(c)

        return model

    def _migrate_agency(self, legacy: dict[str, Any]) -> Any:
        """从 state_history 迁移为 AgencyModel。"""
        from packages.models.self_model import AgencyModel

        model = AgencyModel()
        history = legacy.get("state_history", [])

        for entry in history[-50:]:  # 最近50条
            receipt = ActionReceipt(
                intent_id=entry.get("loop_id", f"legacy_{entry.get('ts', '')}"),
                actor_id="self",
                issued_at=datetime.fromisoformat(entry["ts"]) if entry.get("ts") else datetime.now(timezone.utc),
                status="completed",
                expected_effects=[entry.get("detail", "")],
                observed_effects=[entry.get("focus", "")],
            )
            model.action_history.append(receipt)
            model.self_initiated_count += 1

        return model

    def _migrate_narrative(self, legacy: dict[str, Any]) -> Any:
        """从 perturbations 和 prediction_errors 迁移为 NarrativeModel。"""
        from packages.models.self_model import NarrativeModel

        model = NarrativeModel()

        perturbations = legacy.get("perturbations", [])
        for p in perturbations[-20:]:
            if p.get("cause"):
                node = NarrativeNode(
                    summary=f"Perturbation: {p.get('cause', '')[:200]}",
                    evidence_event_ids=[p.get("loop_id", "")] if p.get("loop_id") else [],
                    confidence=0.6,
                    lessons=[f"Affected fields: {p.get('affected_fields', [])}"],
                )
                try:
                    model.add_node(node)
                except ValueError:
                    pass  # 缺少证据的跳过

        if model.nodes:
            model.current_chapter = f"Migrated from legacy v{legacy.get('_version', 1)}"

        return model

    # ── 保存 ──────────────────────────────────────────────

    def save(self, model: SelfModel, path: str | Path) -> None:
        """保存迁移后的 SelfModel 到文件。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = model.to_dict()
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, default=str)

        logger.info("migrated self_model saved to %s", path)


# ═══════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════

def migrate_self_model(
    source_path: str | Path = "data/self_model.json",
    target_path: str | Path = "data/self_model_v2.json",
    subject_id: str = "eva-001",
) -> SelfModel:
    """一键迁移：读取旧文件 → 转换 → 保存新文件。

    Returns:
        迁移后的 SelfModel
    """
    migrator = SelfModelMigrator(subject_id=subject_id)
    model = migrator.migrate_file(source_path)
    migrator.save(model, target_path)
    return model


def load_self_model(
    path: str | Path = "data/self_model_v2.json",
    fallback_path: str | Path = "data/self_model.json",
    subject_id: str = "eva-001",
) -> SelfModel:
    """加载 SelfModel，优先使用 v2 格式，回退到旧格式迁移。

    用于 bootstrap 中替换现有的 SelfModelStore.load_or_init()。
    """
    v2_path = Path(path)
    if v2_path.exists():
        with v2_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return SelfModel(**data)

    # 回退：从旧格式迁移
    logger.info("v2 self_model not found, migrating from legacy format")
    return migrate_self_model(fallback_path, path, subject_id)
