"""System self-diagnosis and recovery actions.

Runs at bootstrap and on-demand via API. Checks DB integrity,
snapshot validity, config completeness, memory tier health,
and runtime state. Generates a scored DiagnosticReport.

RecoveryActions provide safe repair operations.
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from memory.sqlite_store import SQLiteStore
from memory.tiered_store import TieredMemoryManager
from runtime.file_utils import atomic_json_save


# ── Data classes ────────────────────────────────────────────

@dataclass
class DiagnosticCheck:
    name: str
    passed: bool
    detail: str = ""
    recommendation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiagnosticReport:
    checks: list[DiagnosticCheck] = field(default_factory=list)
    score: int = 100
    overall: str = "healthy"  # healthy | degraded | critical
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    def failed_checks(self) -> list[DiagnosticCheck]:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "overall": self.overall,
            "timestamp": self.timestamp,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "detail": c.detail,
                    "recommendation": c.recommendation,
                }
                for c in self.checks
            ],
            "failed_count": len(self.failed_checks()),
        }


# ── System Diagnostic ───────────────────────────────────────

class SystemDiagnostic:
    def __init__(self, config_paths: list[Path] | None = None) -> None:
        self.config_paths = config_paths or [
            Path("constitution.yaml"),
            Path("config/policy.yaml"),
            Path("config/persona.yaml"),
            Path("config/executors.yaml"),
            Path("config/storage.yaml"),
        ]

    # ── individual checks ───────────────────────────────────

    def check_db(self, store: Any) -> DiagnosticCheck:
        try:
            result = store.fetchall("PRAGMA integrity_check", ())
            integrity_ok = result and result[0].get("integrity_check") == "ok"

            tables = store.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", ()
            )
            table_names = [t["name"] for t in tables]
            count = len(table_names)

            if integrity_ok and count >= 5:
                return DiagnosticCheck(
                    name="db_integrity",
                    passed=True,
                    detail=f"integrity OK, {count} tables: {', '.join(table_names[:12])}",
                    metadata={"tables": count, "table_names": table_names},
                )
            elif integrity_ok:
                return DiagnosticCheck(
                    name="db_integrity",
                    passed=False,
                    detail=f"only {count} tables (expected >= 5)",
                    recommendation="re-run init_db()",
                    metadata={"tables": count},
                )
            else:
                return DiagnosticCheck(
                    name="db_integrity",
                    passed=False,
                    detail=f"PRAGMA integrity_check failed: {result}",
                    recommendation="restore from backup or re-init",
                )
        except Exception as e:
            return DiagnosticCheck(
                name="db_integrity", passed=False,
                detail=str(e), recommendation="check sqlite_store connection",
            )

    def check_snapshot(self, snapshot_path: Path) -> DiagnosticCheck:
        if not snapshot_path.exists():
            return DiagnosticCheck(
                name="snapshot",
                passed=True,  # not critical — first boot
                detail="no snapshot found (normal on first boot)",
                metadata={"path": str(snapshot_path)},
            )
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            version = data.get("version", "unknown")
            has_world = "world_model" in data
            return DiagnosticCheck(
                name="snapshot",
                passed=has_world,
                detail=f"version={version}, has_world_model={has_world}",
                recommendation="" if has_world else "re-save snapshot",
                metadata={"version": version, "size_bytes": os.path.getsize(snapshot_path)},
            )
        except (json.JSONDecodeError, OSError) as e:
            return DiagnosticCheck(
                name="snapshot", passed=False,
                detail=f"corrupt or unreadable: {e}",
                recommendation="delete corrupt snapshot and re-save",
            )

    def check_config(self) -> DiagnosticCheck:
        missing = []
        unparseable = []
        found = []
        for p in self.config_paths:
            if not p.exists():
                missing.append(str(p))
            else:
                try:
                    import yaml
                    with open(p, "r", encoding="utf-8") as f:
                        yaml.safe_load(f)
                    found.append(str(p))
                except Exception:
                    unparseable.append(str(p))

        if not missing and not unparseable:
            return DiagnosticCheck(
                name="config",
                passed=True,
                detail=f"{len(found)} config files valid",
                metadata={"found": found},
            )
        detail_parts = []
        if missing:
            detail_parts.append(f"missing: {', '.join(missing)}")
        if unparseable:
            detail_parts.append(f"unparseable: {', '.join(unparseable)}")
        return DiagnosticCheck(
            name="config",
            passed=len(unparseable) == 0,
            detail="; ".join(detail_parts),
            recommendation="restore missing config files",
            metadata={"missing": missing, "unparseable": unparseable},
        )

    def check_memory_tiers(self, tiered_memory: Any) -> DiagnosticCheck:
        try:
            stats = tiered_memory.stats()
            s1 = stats["S1_session"]["entries"]
            s2 = stats["S2_working"]["entries"]
            s3_act = stats["S3_long_term"]["entries_active"]
            s4_e = stats["S4_world_model"]["entities"]
            s4_ed = stats["S4_world_model"]["edges"]
            s5_ev = stats["S5_event_trace"]["events"]

            issues = []
            if s4_ed > 0 and s4_e == 0:
                issues.append("orphan edges detected (edges without entities)")

            if issues:
                return DiagnosticCheck(
                    name="memory_tiers", passed=False,
                    detail="; ".join(issues),
                    recommendation="run memory maintenance()",
                    metadata={"tiers": stats},
                )
            return DiagnosticCheck(
                name="memory_tiers", passed=True,
                detail=f"S1={s1} S2={s2} S3_active={s3_act} S4_e={s4_e} S4_ed={s4_ed} S5_ev={s5_ev}",
                metadata={"tiers": stats},
            )
        except Exception as e:
            return DiagnosticCheck(
                name="memory_tiers", passed=False,
                detail=str(e), recommendation="check tiered_memory manager",
            )

    def check_runtime(self, system_state: dict[str, Any]) -> DiagnosticCheck:
        pending_events = int(system_state.get("pending_events", 0))
        pending_results = int(system_state.get("pending_results", 0))
        policy_state = system_state.get("policy_state", {})
        policy_current = policy_state.get("state_machine", {}).get("current", "unknown")

        issues = []
        if pending_events > 50:
            issues.append(f"event queue backed up: {pending_events}")
        if pending_results > 20:
            issues.append(f"result registry stale: {pending_results}")
        if policy_current == "quarantined":
            issues.append("system in quarantine")

        if issues:
            return DiagnosticCheck(
                name="runtime",
                passed=False,
                detail="; ".join(issues),
                recommendation="check /health/diagnostic for details, consider /health/recover",
                metadata={
                    "pending_events": pending_events,
                    "pending_results": pending_results,
                    "policy_state": policy_current,
                },
            )
        return DiagnosticCheck(
            name="runtime", passed=True,
            detail=f"events={pending_events} results={pending_results} policy={policy_current}",
        )

    def check_llm(self) -> DiagnosticCheck:
        """Verify a real LLM provider is configured and responding."""
        try:
            from core.llm_adapter import get_llm, get_llm_info, MockLLM
            info = get_llm_info()
            llm = get_llm()
            if isinstance(llm, MockLLM):
                return DiagnosticCheck(
                    name="llm",
                    passed=False,
                    detail=f"no LLM API key configured — using MockLLM (echo mode). Provider: {info['provider']}",
                    recommendation="set ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, or OPENAI_API_KEY environment variable",
                )
            # Lightweight liveness probe — a real API call with minimal tokens
            try:
                reply = llm.chat([
                    {"role": "user", "content": "Respond with exactly the word: OK"},
                ])
                if not reply.strip():
                    return DiagnosticCheck(
                        name="llm",
                        passed=False,
                        detail=f"LLM provider {llm.provider} returned empty response",
                        recommendation="check API account status and rate limits",
                    )
            except Exception as e:
                return DiagnosticCheck(
                    name="llm",
                    passed=False,
                    detail=f"LLM provider {llm.provider} failed liveness probe: {e}",
                    recommendation="check API key validity, network connectivity, and rate limits",
                )
            return DiagnosticCheck(
                name="llm",
                passed=True,
                detail=f"LLM provider: {llm.provider} (liveness OK)",
            )
        except Exception as e:
            return DiagnosticCheck(
                name="llm", passed=False,
                detail=str(e), recommendation="check LLM adapter configuration",
            )

    # ── full diagnostic ─────────────────────────────────────

    def run_full(
        self,
        store: SQLiteStore,
        tiered_memory: TieredMemoryManager | None,
        system_state: dict[str, Any],
        *,
        snapshot_path: Path | None = None,
    ) -> DiagnosticReport:
        checks: list[DiagnosticCheck] = []

        # 1. DB
        checks.append(self.check_db(store))

        # 2. Snapshot
        if snapshot_path:
            checks.append(self.check_snapshot(snapshot_path))

        # 3. Config
        checks.append(self.check_config())

        # 4. Memory tiers
        if tiered_memory:
            checks.append(self.check_memory_tiers(tiered_memory))

        # 5. Runtime
        checks.append(self.check_runtime(system_state))

        # 6. LLM
        checks.append(self.check_llm())

        # score
        passed = sum(1 for c in checks if c.passed)
        score = round(passed / len(checks) * 100) if checks else 100
        if score == 100:
            overall = "healthy"
        elif score >= 60:
            overall = "degraded"
        else:
            overall = "critical"

        return DiagnosticReport(checks=checks, score=score, overall=overall)


# ── Recovery Actions ────────────────────────────────────────

class RecoveryActions:
    @staticmethod
    def recover_db(store: Any) -> DiagnosticCheck:
        """Safe re-init: CREATE IF NOT EXISTS for all tables."""
        try:
            store.init_db()
            return DiagnosticCheck(
                name="recover_db", passed=True,
                detail="db re-initialized (CREATE IF NOT EXISTS)",
            )
        except Exception as e:
            return DiagnosticCheck(
                name="recover_db", passed=False,
                detail=str(e),
                recommendation="manual db repair required",
            )

    @staticmethod
    def recover_snapshot(
        snapshot_path: Path, world_model: Any = None
    ) -> DiagnosticCheck:
        """Regenerate a valid snapshot from current state."""
        try:
            data = {"version": "0.1", "world_model": {}, "proactive_state": {}}
            if world_model:
                data["world_model"] = world_model.to_dict()
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json_save(snapshot_path, data)
            return DiagnosticCheck(
                name="recover_snapshot", passed=True,
                detail=f"snapshot rebuilt at {snapshot_path}",
            )
        except Exception as e:
            return DiagnosticCheck(
                name="recover_snapshot", passed=False,
                detail=str(e),
                recommendation="check disk space and permissions",
            )

    @staticmethod
    def reset_policy(policy_engine: Any) -> DiagnosticCheck:
        """Reset policy state to Dormant (exit quarantine)."""
        if policy_engine is None:
            return DiagnosticCheck(
                name="reset_policy", passed=False,
                detail="policy engine not available",
            )
        policy_engine.transition("manual_intervention")
        current = policy_engine.state_machine.current.value
        return DiagnosticCheck(
            name="reset_policy", passed=True,
            detail=f"policy state reset to {current}",
        )

    @staticmethod
    def clear_stale_registry(result_registry: Any) -> DiagnosticCheck:
        """Clean up stale result registry entries."""
        if result_registry is None:
            return DiagnosticCheck(
                name="clear_registry", passed=False,
                detail="result registry not available",
            )
        before = result_registry.size()
        result_registry.cleanup(ttl_sec=0)  # immediate
        after = result_registry.size()
        return DiagnosticCheck(
            name="clear_registry", passed=True,
            detail=f"cleared {before - after} stale entries ({before} → {after})",
        )
