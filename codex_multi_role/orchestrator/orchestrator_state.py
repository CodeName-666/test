"""State tracking utilities for the Codex orchestrator."""
from __future__ import annotations

from typing import Any, Dict

from ..roles.role_spec import RoleSpec
from ..turn_result import TurnResult


class OrchestratorState:
    """Track orchestrator state across turns.

    Designed for extension by overriding the protected methods
    `_build_initial_state`, `_update_state`, and `_role_signaled_done`.
    """

    def __init__(self, goal: str) -> None:
        """Initialize the state tracker for a run.

        Args:
            goal: Goal text for the run. Expected to be a validated non-empty string.

        Returns:
            None.

        Side Effects:
            Initializes the in-memory state dictionary.
        """
        self.state: Dict[str, Any] = self._build_initial_state(goal)

    def _build_initial_state(self, goal: str) -> Dict[str, Any]:
        """Build the initial state structure for a run."""
        return {
            "goal": goal,
            "latest_json_by_role": {},
            "history": [],
        }

    def _update_state(
        self,
        role_name: str,
        turn: TurnResult,
        reduced_payload: Dict[str, Any],
    ) -> None:
        """Record the latest payload and history for a role."""
        self.state["latest_json_by_role"][role_name] = reduced_payload
        self.state["history"].append(
            {"role": role_name, "turn": turn.request_id, "handoff": reduced_payload}
        )

    def _role_signaled_done(
        self,
        role_spec: RoleSpec,
        reduced_payload: Dict[str, Any],
    ) -> bool:
        """Check whether a role signaled completion, if allowed."""
        status_value = reduced_payload.get("status")
        is_done_signal = (
            isinstance(status_value, str) and status_value.strip().upper() == "DONE"
        )
        return role_spec.behaviors.can_finish and is_done_signal
