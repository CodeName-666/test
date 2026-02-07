"""High-level planner-gated communication engine."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from .coordinator import CommunicationCoordinator
from .decision import PlannerDecision
from .feedback import AgentFeedback
from .ports import ExecutionResultLike, LoggerPort


class CommunicationEngine:
    """Run planner-centric communication cycles.

    The engine owns the communication loop while orchestration-specific
    mechanics (planner calls, delegation execution, status updates,
    lifecycle hooks) are provided as callables.
    """

    def __init__(
        self,
        run_id: str,
        goal: str,
        max_iterations: int,
        initial_wave_index: int,
        logger: LoggerPort,
        coordinator: CommunicationCoordinator,
        start_clients: Callable[[], None],
        stop_clients: Callable[[], None],
        run_planner: Callable[[Dict[str, Any]], PlannerDecision],
        execute_delegations: Callable[[List[Dict[str, Any]]], List[ExecutionResultLike]],
        update_delegation_status: Callable[[AgentFeedback], None],
        record_manifest_event: Callable[[str, Dict[str, Any], Optional[str]], None],
        build_idempotency_key: Callable[[str, Dict[str, Any]], str],
        persist_controller_state: Callable[[], None],
    ) -> None:
        """Initialize communication engine.

        Args:
            run_id: Current run identifier.
            goal: Run goal.
            max_iterations: Maximum planner iterations.
            initial_wave_index: Already persisted wave index for resume mode.
            logger: Logger interface.
            coordinator: Communication coordinator.
            start_clients: Hook to start planner/worker clients.
            stop_clients: Hook to stop planner/worker clients.
            run_planner: Hook to execute planner and parse decision.
            execute_delegations: Hook to execute delegations.
            update_delegation_status: Hook to update delegation lifecycle.
            record_manifest_event: Hook to persist manifest events.
            build_idempotency_key: Hook to build idempotency keys.
            persist_controller_state: Hook to persist controller state.
        """
        self._run_id = run_id
        self._goal = goal
        self._max_iterations = max_iterations
        self._initial_wave_index = initial_wave_index
        self._logger = logger
        self._coordinator = coordinator
        self._start_clients = start_clients
        self._stop_clients = stop_clients
        self._run_planner = run_planner
        self._execute_delegations = execute_delegations
        self._update_delegation_status = update_delegation_status
        self._record_manifest_event = record_manifest_event
        self._build_idempotency_key = build_idempotency_key
        self._persist_controller_state = persist_controller_state
        self._wave_counter = self._initial_wave_index

    def run(self) -> Dict[str, Any]:
        """Execute the communication loop.

        Returns:
            Final context after loop termination.
        """
        context: Dict[str, Any] = {}
        self._logger.log(f"Starting DynamicOrchestrator run: {self._run_id}")
        self._logger.log(f"Goal: {self._goal}")
        self._start_clients()
        self._record_manifest_event(
            "run_started",
            {"goal": self._goal},
            idempotency_key=f"run_started:{self._run_id}",
        )
        try:
            context = self._coordinator.build_initial_context(self._goal)
            for iteration in range(self._max_iterations):
                self._logger.log(f"\n=== Iteration {iteration + 1} ===")
                decision = self._run_planner(context)
                self._record_manifest_event(
                    "planner_decision",
                    decision.raw_payload,
                    idempotency_key=self._build_idempotency_key(
                        "planner_decision",
                        {"iteration": iteration + 1, "payload": decision.raw_payload},
                    ),
                )
                self._logger.log(
                    f"Planner decision: action={decision.action}, status={decision.status}"
                )
                if decision.is_done:
                    self._logger.log("Planner signaled DONE. Completing run.")
                    break

                if decision.needs_user_input:
                    critical_questions = self._coordinator.extract_critical_questions(decision.questions)
                    if critical_questions:
                        user_answers = self._coordinator.handle_user_questions(critical_questions)
                        context = self._coordinator.merge_user_answers(context, user_answers)
                        continue
                    self._logger.log(
                        "Planner requested user input without critical questions; "
                        "continuing without user interaction."
                    )
                    self._record_manifest_event(
                        "planner_policy_violation",
                        {
                            "reason": "needs_user_input without critical questions",
                            "questions": [question.question for question in decision.questions],
                        },
                        idempotency_key=self._build_idempotency_key(
                            "planner_policy_violation",
                            {"iteration": iteration + 1},
                        ),
                    )

                if decision.delegations:
                    self._logger.log(f"Executing {len(decision.delegations)} delegations")
                    wave_start = time.time()
                    results = self._execute_delegations(decision.delegations)
                    feedbacks, pending_questions = self._coordinator.process_execution_results(
                        results,
                        update_delegation_status=self._update_delegation_status,
                    )
                    if pending_questions:
                        self._logger.log(
                            f"Collected {len(pending_questions)} planner-facing question(s) from workers."
                        )
                        context = self._coordinator.merge_pending_questions(
                            context,
                            pending_questions,
                        )
                    context = self._coordinator.merge_delegation_results(context, results)
                    wave_duration = time.time() - wave_start
                    self._wave_counter = self._coordinator.persist_wave_outputs(
                        context=context,
                        decision=decision,
                        feedbacks=feedbacks,
                        results=results,
                        wave_duration_s=wave_duration,
                        current_wave_index=self._wave_counter,
                    )
                else:
                    context["iteration"] = context.get("iteration", 0) + 1
            else:
                self._logger.log("Maximum iterations reached. Stopping.")
        finally:
            self._persist_controller_state()
            self._record_manifest_event(
                "run_finished",
                {"state": "stopped"},
                idempotency_key=f"run_finished:{self._run_id}",
            )
            self._stop_clients()
        self._logger.log(f"DynamicOrchestrator run completed: {self._run_id}")
        return context

    @property
    def wave_counter(self) -> int:
        """Return the last persisted wave index."""
        return self._wave_counter


__all__ = ["CommunicationEngine"]
