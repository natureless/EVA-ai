"""Verifier — MVSC 行动结果验证器。

实现 VerifierProtocol，在 AgentOS 执行完成后验证:
1. 事实检查: 结果是否与预期一致
2. 格式检查: 输出格式是否有效
3. 状态一致性: 副作用是否在允许范围内
4. 安全边界: 是否触碰了禁止区域

验证失败不阻止流水线，但会:
- 发出 verification.failed 事件
- 降低 CapabilityModel 中对应能力的置信度
- 记录到审计日志
"""

from __future__ import annotations

import logging
from typing import Any

from packages.contracts.events import EventEnvelope, EventFamily
from packages.contracts.protocols import (
    Intent,
    Plan,
    ToolResult,
    VerificationResult,
)

logger = logging.getLogger("eva.verifier")


class Verifier:
    """行动结果验证器。

    Usage::

        verifier = Verifier()
        result = await verifier.verify(intent, plan, tool_result)
        if not result.passed:
            logger.warning("verification failed: %s", result.summary)
    """

    def __init__(
        self,
        strict_mode: bool = False,
        max_output_size: int = 100_000,
    ) -> None:
        self.strict_mode = strict_mode
        self.max_output_size = max_output_size
        self._total_checks = 0
        self._failed_checks = 0

    async def verify(
        self,
        intent: Intent,
        plan: Plan | None,
        result: ToolResult,
    ) -> VerificationResult:
        """验证行动结果。

        检查项:
        1. 基本有效性: result.ok, error 字段
        2. 输出大小: 不超过 max_output_size
        3. 副作用: 是否包含禁止的操作
        4. 意图匹配: 结果是否与原始意图相关
        """
        checks: list[dict[str, Any]] = []
        passed = True

        # ── Check 1: 基本有效性 ──
        if not result.ok:
            checks.append({
                "check": "basic_validity",
                "passed": False,
                "detail": f"Tool execution failed: {result.error}",
            })
            passed = False
        else:
            checks.append({
                "check": "basic_validity",
                "passed": True,
                "detail": "Tool execution succeeded",
            })

        # ── Check 2: 输出大小 ──
        output_size = len(str(result.data))
        if output_size > self.max_output_size:
            checks.append({
                "check": "output_size",
                "passed": False,
                "detail": f"Output {output_size} bytes exceeds max {self.max_output_size}",
            })
            if self.strict_mode:
                passed = False
        else:
            checks.append({
                "check": "output_size",
                "passed": True,
                "detail": f"Output size {output_size} bytes OK",
            })

        # ── Check 3: 副作用 ──
        forbidden_effects = {"delete_critical", "privilege_escalation", "modify_constitution"}
        dangerous_side_effects = [
            se for se in result.side_effects
            if any(fe in se for fe in forbidden_effects)
        ]
        if dangerous_side_effects:
            checks.append({
                "check": "side_effects",
                "passed": False,
                "detail": f"Forbidden side effects: {dangerous_side_effects}",
            })
            passed = False
        else:
            checks.append({
                "check": "side_effects",
                "passed": True,
                "detail": f"Side effects OK ({len(result.side_effects)} effects)",
            })

        # ── Check 4: 意图匹配（宽松检查）──
        if intent and result.data:
            intent_words = set(intent.description.lower().split())
            result_str = str(result.data).lower()
            overlap = sum(1 for w in intent_words if w in result_str)
            match_ratio = overlap / len(intent_words) if intent_words else 1.0
            checks.append({
                "check": "intent_match",
                "passed": match_ratio > 0.1,
                "detail": f"Intent match ratio: {match_ratio:.2f}",
            })
        else:
            checks.append({
                "check": "intent_match",
                "passed": True,
                "detail": "No intent to match against",
            })

        # ── 汇总 ──
        self._total_checks += 1
        if not passed:
            self._failed_checks += 1

        failed_details = [c["detail"] for c in checks if not c["passed"]]
        summary = (
            f"Verification {'PASSED' if passed else 'FAILED'}: "
            + "; ".join(failed_details) if failed_details else "all checks passed"
        )

        return VerificationResult(
            passed=passed,
            checks=checks,
            summary=summary,
        )

    def to_events(
        self,
        result: VerificationResult,
        causation_id: str,
    ) -> list[EventEnvelope]:
        """将验证结果转换为事件。"""
        if result.passed:
            return [EventEnvelope(
                event_type=EventFamily.VERIFICATION.PASSED,
                source="verifier",
                causation_id=causation_id,
                payload={"checks": len(result.checks), "summary": result.summary},
            )]
        else:
            return [EventEnvelope(
                event_type=EventFamily.VERIFICATION.FAILED,
                source="verifier",
                causation_id=causation_id,
                payload={
                    "checks": len(result.checks),
                    "failed_checks": [c for c in result.checks if not c["passed"]],
                    "summary": result.summary,
                },
            )]

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_checks": self._total_checks,
            "failed_checks": self._failed_checks,
            "pass_rate": (
                1.0 - self._failed_checks / self._total_checks
                if self._total_checks > 0 else 1.0
            ),
        }
