"""Actual agent invocation intents bound to immutable context references."""

from uuid import uuid4
from datetime import datetime
from typing import Any, Callable

from event.codec import from_legacy_event
from memory.context_evidence import ActionContextEvidence
from packages.contracts.actions import ActionIntent, ActionReceipt
from packages.contracts.state import StateDelta
from packages.kernel.sqlite_state_repository import SQLiteStateRepository, checked_state


class RequestActionContext(SQLiteStateRepository):
    enable_action_context: bool
    subject_id: str
    _claims: dict[str, str]
    _agent_claims: dict[str, dict]
    _record: Callable[[str | None], Any]
    _now: Callable[[], datetime]

    def begin_agent_action(self, event, agent, evidence):
        if not self.enable_action_context:
            return None
        if not isinstance(agent, str) or not 1 <= len(agent) <= 128:
            raise ValueError("invalid agent identity")
        inputs = ActionContextEvidence.model_validate(evidence)
        if inputs.world is None or inputs.memory is None:
            raise ValueError(
                "agent context requires actual world and memory references"
            )
        with self._transaction():
            record = self._record(event.correlation_id)
            if (
                record is None
                or record.event_id != event.id
                or record.status != "claimed"
                or self._claims.get(record.task_id) is None
                or self._now() >= record.deadline_at
            ):
                raise ValueError(
                    "agent invocation requires a live durable request claim"
                )
            # The full event is checked independently of the cache's identity.
            from packages.kernel.request_dispatch_store import source_hash

            if source_hash(event) != record.source_hash:
                raise ValueError("agent action source mismatch")
            if self.action_for_source(
                self.subject_id, record.event_id, "agent_invocation"
            ):
                raise ValueError("agent invocation already has an intent")
            intent = ActionIntent(
                source_event_id=record.event_id,
                slot="agent_invocation",
                tool_id="runtime:agent_invocation",
                parameters={
                    "task_id": record.task_id,
                    "parent_action_id": record.action_id,
                    "selected_agent": agent,
                    "inputs": inputs.model_dump(mode="json"),
                },
            )
            state = self._load(self.subject_id)
            planned = checked_state(
                StateDelta(
                    base_version=state.version,
                    source="agent_context",
                    changes={
                        "health": {
                            **state.health,
                            "agent_context": {
                                "task_id": record.task_id,
                                "action_id": intent.action_id,
                                "context_hash": inputs.context_hash,
                            },
                        }
                    },
                ).apply(state)
            )
            self._commit(
                state.version,
                planned,
                self._events(
                    [from_legacy_event(event, subject_id=self.subject_id)],
                    self.subject_id,
                ),
                [intent],
            )
            _, receipt, _ = self._action(intent.action_id)
            claimed = ActionReceipt.model_validate(
                {
                    **receipt.model_dump(),
                    "status": "executing",
                    "started_at": self._now(),
                    "claim_token": f"claim_{uuid4().hex}",
                }
            )
            self._save_receipt(claimed)
        binding = {
            "action_id": intent.action_id,
            "claim_token": claimed.claim_token,
            "state_version": claimed.state_version,
            "request_hash": intent.request_hash,
        }
        with self._lock:
            self._agent_claims[record.task_id] = binding
        return binding.copy()

    def finish_agent_action(self, event, binding, *, returned_ok):
        if binding is None:
            return
        with self._lock:
            known = self._agent_claims.get(event.correlation_id)
            if known != binding:
                raise ValueError("agent completion claim mismatch")
            record = self._record(event.correlation_id)
            if record is None or record.event_id != event.id:
                raise ValueError("agent completion source mismatch")
            self.finish_action(
                binding["action_id"],
                binding["claim_token"],
                returned_ok=returned_ok,
                observation_kind="handler_exception"
                if returned_ok is None
                else "handler_return",
            )
            self._agent_claims.pop(event.correlation_id, None)

    def seal_agent_action(self, task_id):
        """Do not infer an agent return from the enclosing processor's receipt."""
        binding = self._agent_claims.pop(task_id, None)
        if binding is not None:
            with self._transaction():
                _, receipt, _ = self._action(binding["action_id"])
                if receipt.status == "executing":
                    self._save_receipt(
                        ActionReceipt.model_validate(
                            {
                                **receipt.model_dump(),
                                "status": "unknown",
                                "observation_kind": "recovery_unknown",
                                "sealed_at": self._now(),
                            }
                        )
                    )

    def agent_context_for_task(self, task_id):
        """Read-only; no expiry advancement, notification delivery or execution."""
        with self._lock:
            self._check_open()
            record = self._record(task_id)
            if record is None:
                return None
            action = self.action_for_source(
                self.subject_id, record.event_id, "agent_invocation"
            )
            if action is None:
                return None
            intent, receipt, _ = action
            parameters = intent.parameters
            if (
                intent.tool_id != "runtime:agent_invocation"
                or set(parameters)
                != {"task_id", "parent_action_id", "selected_agent", "inputs"}
                or parameters["task_id"] != record.task_id
                or parameters["parent_action_id"] != record.action_id
            ):
                raise ValueError("agent intent parent mismatch")
            inputs = ActionContextEvidence.model_validate(parameters["inputs"])
            if inputs.world is None or inputs.memory is None:
                raise ValueError("agent input references unavailable")
            return {
                "task_id": record.task_id,
                "event_id": record.event_id,
                "parent_action_id": record.action_id,
                "selected_agent": parameters["selected_agent"],
                "inputs": inputs.model_dump(mode="json"),
                "intent": {
                    "action_id": intent.action_id,
                    "state_version": receipt.state_version,
                    "request_hash": intent.request_hash,
                },
                "observation": receipt.model_dump(mode="json", exclude={"claim_token"}),
                "external_actions_replayed": False,
            }
