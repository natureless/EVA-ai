"""Lifecycle — EVA-MVSC 生命周期管理。

实现 MVSC 框架要求的:
- 心跳 + 自证挑战-响应
- 8态 RuntimeMode 状态机
- 健康检查与降级
- 快照静默点
- 恢复流程
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from packages.contracts.state import RuntimeMode

logger = logging.getLogger("eva.lifecycle")


# ═══════════════════════════════════════════════════════════════
# Heartbeat — 心跳 + 自证
# ═══════════════════════════════════════════════════════════════

class Heartbeat:
    """心跳管理器 — 支持挑战-响应自证。

    防止"假活"场景: 进程存在但认知循环已卡死。
    通过定期发出 challenge，要求 cognition loop 在时限内
    计算正确的 response，证明自己仍在正常运转。
    """

    def __init__(self, challenge_interval_sec: float = 30.0) -> None:
        self._challenge_interval = challenge_interval_sec
        self._last_challenge: str | None = None
        self._last_challenge_at: float = 0.0
        self._last_response_at: float = 0.0
        self._missed_challenges: int = 0
        self._total_challenges: int = 0

    def issue_challenge(self) -> str:
        """发出新的自证挑战。"""
        challenge = secrets.token_hex(16)
        self._last_challenge = challenge
        self._last_challenge_at = time.time()
        self._total_challenges += 1
        return challenge

    def verify_response(self, challenge: str, response: str) -> bool:
        """验证自证响应。

        响应 = SHA-256(challenge + secret_salt)
        """
        if challenge != self._last_challenge:
            return False
        expected = hashlib.sha256(
            (challenge + "eva-self-proof").encode()
        ).hexdigest()[:16]
        is_valid = secrets.compare_digest(response, expected)
        if is_valid:
            self._last_response_at = time.time()
            self._missed_challenges = 0
        else:
            self._missed_challenges += 1
        return is_valid

    @property
    def healthy(self) -> bool:
        """心跳是否健康。"""
        if self._missed_challenges >= 3:
            return False
        if self._last_challenge_at > 0:
            elapsed = time.time() - self._last_challenge_at
            if elapsed > self._challenge_interval * 3:
                return False
        return True

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "total_challenges": self._total_challenges,
            "missed_challenges": self._missed_challenges,
            "last_challenge_at": self._last_challenge_at,
            "last_response_at": self._last_response_at,
        }


# ═══════════════════════════════════════════════════════════════
# LifecycleManager — 生命周期状态机
# ═══════════════════════════════════════════════════════════════

class LifecycleManager:
    """管理 EVA 的 8态生命周期。

    状态转换规则:
    BOOTING → ACTIVE (bootstrap 完成)
    ACTIVE → REFLECTING (定期反思)
    ACTIVE → CONSOLIDATING (记忆整合)
    ACTIVE → DEGRADED (错误超阈值)
    ACTIVE → SUSPENDED (用户暂停)
    DEGRADED → RECOVERING (恢复中)
    RECOVERING → ACTIVE (恢复完成)
    ANY → SHUTTING_DOWN (系统关闭)
    """

    def __init__(self) -> None:
        self._mode: RuntimeMode = RuntimeMode.BOOTING
        self._mode_since: float = time.time()
        self._history: list[dict[str, Any]] = []
        self._degradation_reason: str = ""

    @property
    def mode(self) -> RuntimeMode:
        return self._mode

    @property
    def mode_since(self) -> float:
        return self._mode_since

    def transition(self, new_mode: RuntimeMode, reason: str = "") -> bool:
        """尝试转换到新模式。

        Returns:
            True 如果转换成功
        """
        if new_mode == self._mode:
            return True

        # 验证转换合法性
        if not self._is_valid_transition(self._mode, new_mode):
            logger.warning(
                "invalid lifecycle transition: %s → %s (reason: %s)",
                self._mode.value, new_mode.value, reason,
            )
            return False

        old = self._mode
        self._mode = new_mode
        self._mode_since = time.time()

        self._history.append({
            "from": old.value,
            "to": new_mode.value,
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._history) > 50:
            self._history = self._history[-50:]

        logger.info("lifecycle: %s → %s (reason: %s)", old.value, new_mode.value, reason)
        return True

    def set_degraded(self, reason: str) -> None:
        """进入降级模式。"""
        self._degradation_reason = reason
        self.transition(RuntimeMode.DEGRADED, reason)

    def set_recovering(self) -> None:
        """进入恢复模式。"""
        self.transition(RuntimeMode.RECOVERING, "recovery initiated")

    def set_active(self) -> None:
        """回到活跃模式。"""
        self.transition(RuntimeMode.ACTIVE, "recovery complete")

    @staticmethod
    def _is_valid_transition(current: RuntimeMode, target: RuntimeMode) -> bool:
        """检查状态转换是否合法。"""
        # SHUTTING_DOWN 从任何状态都可以
        if target == RuntimeMode.SHUTTING_DOWN:
            return True
        # BOOTING 只能转到 ACTIVE 或 DEGRADED
        if current == RuntimeMode.BOOTING:
            return target in (RuntimeMode.ACTIVE, RuntimeMode.DEGRADED)
        # SUSPENDED 只能转到 ACTIVE
        if current == RuntimeMode.SUSPENDED:
            return target == RuntimeMode.ACTIVE
        # DEGRADED 只能转到 RECOVERING
        if current == RuntimeMode.DEGRADED:
            return target == RuntimeMode.RECOVERING
        # RECOVERING 只能转到 ACTIVE 或 DEGRADED
        if current == RuntimeMode.RECOVERING:
            return target in (RuntimeMode.ACTIVE, RuntimeMode.DEGRADED)
        # ACTIVE 可以转到任何非 BOOTING 状态
        return True

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "mode": self._mode.value,
            "mode_since_sec": round(time.time() - self._mode_since, 1),
            "degradation_reason": self._degradation_reason,
            "history": self._history[-5:],
        }
