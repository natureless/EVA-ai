"""Dispatch committed actions once per claim; unknown effects require reconciliation."""

import asyncio
import logging

logger = logging.getLogger("eva.action_dispatcher")


class ActionDispatcher:
    def __init__(self, repository):
        self.repository = repository

    async def dispatch(self, action_id, handler):
        """The injected handler must apply current policy and tool boundaries.

        It receives detached committed intent/state and returns a dict with a
        boolean ok. A return is an observation, never external-state certification.
        """
        with self.repository.dispatch_scope():
            try:
                claimed = await asyncio.to_thread(
                    self.repository.claim_action, action_id
                )
            except Exception:
                logger.error("durable action claim unavailable; handler not called")
                return {
                    "ok": False,
                    "execution_state": "not_started",
                    "error": "action_claim_unavailable",
                }
            if claimed is None:
                return {
                    "ok": False,
                    "execution_state": "not_started",
                    "error": "action_already_claimed",
                }
            intent, receipt, state = claimed
            try:
                result = await handler(intent, state)
            except BaseException:
                try:
                    await asyncio.to_thread(
                        self.repository.finish_action,
                        action_id,
                        receipt.claim_token,
                        returned_ok=None,
                        observation_kind="handler_exception",
                    )
                except Exception:
                    logger.error(
                        "action exception observation unavailable; claim retained"
                    )
                raise
            returned_ok = (
                result.get("ok")
                if isinstance(result, dict) and type(result.get("ok")) is bool
                else None
            )
            try:
                accepted = await asyncio.to_thread(
                    self.repository.finish_action,
                    action_id,
                    receipt.claim_token,
                    returned_ok=returned_ok,
                )
            except Exception:
                logger.error(
                    "action return observation unavailable; effect outcome unknown"
                )
                return {
                    "ok": False,
                    "execution_state": "outcome_unknown",
                    "error": "action_receipt_unavailable",
                }
            if not accepted:
                return {
                    "ok": False,
                    "execution_state": "outcome_unknown",
                    "error": "action_terminal_already_recorded",
                }
            return result
