"""Dynamic orchestrator with planner-gated multi-agent communication.

The planner remains the single decision source. Workers never communicate
directly with each other or with the user. All data flow passes through the
orchestrator and planner context.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..client.codex_role_client import CodexRoleClient
from ..logging import TimestampLogger
from ..prompt_builder import PromptBuilder
from ..roles.role_client import RoleClient
from ..roles.role_spec import RoleSpec, RoleSpecCatalog
from ..sequential.file_applier import FileApplier
from ..sequential.orchestrator_config import OrchestratorConfig
from ..sequential.orchestrator_state import OrchestratorState
from ..timeout_resolver import TimeoutResolver
from ..turn_result import TurnResult
from ..utils.env_utils import EnvironmentReader
from ..utils.json_utils import JsonPayloadFormatter
from .agent_registry import AgentRegistry, redact_secrets
from .delegation_manager import Delegation, DelegationManager, DelegationStatus
from .feedback_loop import AgentFeedback, FeedbackLoop, FeedbackStatus
from .parallel_executor import ExecutionResult, ParallelExecutor
from .role_client_factory import RoleClientFactory
from .run_store import RunStore
from .runtime_models import ContextPacket, DetailIndexEntry, build_question_id
from .user_interaction import Answer, ConsoleUserInteraction, Question, UserInteraction


@dataclass
class PlannerDecision:
    """Decision payload returned by planner.

    Attributes:
        summary: Short decision summary.
        needs_user_input: Whether planner requests user input before continuing.
        questions: Planner questions for the user.
        delegations: Delegation specifications for worker agents.
        action: Decision action (`delegate`, `ask_user`, or `done`).
        status: Planner status (`CONTINUE` or `DONE`).
        planner_decision: Structured planner I/O status block.
        wave_compact_md: Optional planner compact wave markdown.
        wave_detailed_md: Optional planner detailed wave markdown.
        raw_payload: Raw planner payload.
    """

    summary: str = ""
    needs_user_input: bool = False
    questions: List[Question] = field(default_factory=list)
    delegations: List[Dict[str, Any]] = field(default_factory=list)
    action: str = "delegate"
    status: str = "CONTINUE"
    planner_decision: Dict[str, Any] = field(default_factory=dict)
    wave_compact_md: str = ""
    wave_detailed_md: str = ""
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "PlannerDecision":
        """Create decision from planner payload.

        Args:
            payload: Parsed planner payload.

        Returns:
            Normalized planner decision object.
        """
        questions = cls._parse_questions(payload)
        planner_decision_value = cls._normalize_dict(payload.get("planner_decision"))
        delegations_value = cls._normalize_list(payload.get("delegations"))
        wave_compact_md = cls._normalize_str(payload.get("wave_compact_md"))
        wave_detailed_md = cls._normalize_str(payload.get("wave_detailed_md"))
        summary_value = cls._normalize_str(payload.get("summary"))
        action_value = cls._normalize_str(payload.get("action"), default="delegate")
        status_value = cls._normalize_str(payload.get("status"), default="CONTINUE")
        needs_user_input = bool(payload.get("needs_user_input", False))
        result = cls(
            summary=summary_value,
            needs_user_input=needs_user_input,
            questions=questions,
            delegations=delegations_value,
            action=action_value,
            status=status_value,
            planner_decision=planner_decision_value,
            wave_compact_md=wave_compact_md,
            wave_detailed_md=wave_detailed_md,
            raw_payload=payload,
        )
        return result

    @classmethod
    def _parse_questions(cls, payload: Dict[str, Any]) -> List[Question]:
        questions: List[Question] = []
        raw_questions = payload.get("questions", [])
        if isinstance(raw_questions, list):
            for index, raw_question in enumerate(raw_questions):
                question = cls._build_question(raw_question, index)
                if question is not None:
                    questions.append(question)
        return questions

    @staticmethod
    def _normalize_dict(value: Any) -> Dict[str, Any]:
        result: Dict[str, Any]
        if isinstance(value, dict):
            result = value
        else:
            result = {}
        return result

    @staticmethod
    def _normalize_list(value: Any) -> List[Any]:
        result: List[Any]
        if isinstance(value, list):
            result = value
        else:
            result = []
        return result

    @staticmethod
    def _normalize_str(value: Any, default: str = "") -> str:
        result: str
        if isinstance(value, str):
            result = value
        elif value is None:
            result = default
        else:
            result = str(value)
        return result

    @staticmethod
    def _build_question(raw_question: Any, index: int) -> Optional[Question]:
        question: Optional[Question] = None
        if isinstance(raw_question, dict):
            question_text = raw_question.get("question", "")
            if isinstance(question_text, str) and question_text.strip():
                source = raw_question.get("source", f"planner_{index}")
                if not isinstance(source, str):
                    source = f"planner_{index}"
                if not source.strip():
                    source = f"planner_{index}"
                normalized_id = build_question_id(question_text, source)
                category = raw_question.get("category", "optional")
                if not isinstance(category, str):
                    category = "optional"
                default_suggestion = raw_question.get("default_suggestion")
                if default_suggestion is not None and not isinstance(default_suggestion, str):
                    default_suggestion = str(default_suggestion)
                context = raw_question.get("context")
                if context is not None and not isinstance(context, str):
                    context = str(context)
                priority = raw_question.get("priority", "normal")
                if not isinstance(priority, str):
                    priority = "normal"
                expected_format = raw_question.get("expected_answer_format", "text")
                if not isinstance(expected_format, str):
                    expected_format = "text"
                question = Question(
                    id=normalized_id,
                    question=question_text,
                    category=category,
                    default_suggestion=default_suggestion,
                    context=context,
                    priority=priority,
                    expected_answer_format=expected_format,
                )
        return question

    @property
    def is_done(self) -> bool:
        """Check if planner signaled completion."""
        status_done = self.status.strip().upper() == "DONE"
        action_done = self.action.strip().lower() == "done"
        result = status_done or action_done
        return result


class DynamicOrchestrator:
    """Planner-gated dynamic orchestrator implementation."""

    def __init__(
        self,
        role_specifications: List[RoleSpec],
        configuration: OrchestratorConfig,
        user_interaction: Optional[UserInteraction] = None,
        environment_reader: Optional[EnvironmentReader] = None,
        json_formatter: Optional[JsonPayloadFormatter] = None,
        logger: Optional[TimestampLogger] = None,
        role_spec_catalog: Optional[RoleSpecCatalog] = None,
        max_parallel_workers: int = 4,
    ) -> None:
        """Initialize dynamic orchestrator.

        Args:
            role_specifications: Available role specifications.
            configuration: Runtime configuration.
            user_interaction: User interaction adapter.
            environment_reader: Optional environment reader override.
            json_formatter: Optional JSON formatter override.
            logger: Optional logger override.
            role_spec_catalog: Optional role specification catalog override.
            max_parallel_workers: Maximum parallel worker executions.
        """
        self._initialize_shared_dependencies(
            user_interaction=user_interaction,
            environment_reader=environment_reader,
            json_formatter=json_formatter,
            logger=logger,
            role_spec_catalog=role_spec_catalog,
        )
        self.configuration = configuration
        self._initialize_role_components(
            role_specifications=role_specifications,
            max_parallel_workers=max_parallel_workers,
        )
        self._initialize_run_components(
            max_parallel_workers=max_parallel_workers,
            goal=configuration.goal,
        )
        self._initialize_execution_counters()
        self._initialize_manifest_state()

    def _initialize_shared_dependencies(
        self,
        user_interaction: Optional[UserInteraction],
        environment_reader: Optional[EnvironmentReader],
        json_formatter: Optional[JsonPayloadFormatter],
        logger: Optional[TimestampLogger],
        role_spec_catalog: Optional[RoleSpecCatalog],
    ) -> None:
        self._environment_reader = environment_reader or EnvironmentReader()
        self._json_formatter = json_formatter or JsonPayloadFormatter()
        self._logger = logger or TimestampLogger()
        self._role_spec_catalog = role_spec_catalog or RoleSpecCatalog(
            environment_reader=self._environment_reader
        )
        self._user_interaction = user_interaction or ConsoleUserInteraction()

    def _initialize_role_components(
        self,
        role_specifications: List[RoleSpec],
        max_parallel_workers: int,
    ) -> None:
        self.role_specs_by_name = self._build_role_spec_index(role_specifications)
        self._planner_spec = self._find_planner_spec(role_specifications)
        if self._planner_spec is None:
            raise ValueError("No planner role found in role_specifications")
        self._agent_specs = {
            role_name: spec
            for role_name, spec in self.role_specs_by_name.items()
            if role_name != self._planner_spec.name
        }
        self._agent_registry = AgentRegistry.from_role_specs(self._agent_specs)
        self._delegation_manager = DelegationManager(
            available_agents=set(self._agent_specs.keys())
        )
        self._feedback_loop = FeedbackLoop(user_interaction=self._user_interaction)
        self._parallel_executor = ParallelExecutor(max_workers=max_parallel_workers)
        self._timeout_resolver = TimeoutResolver(self._environment_reader)
        self._prompt_builder = PromptBuilder(
            role_spec_catalog=self._role_spec_catalog,
            json_formatter=self._json_formatter,
            role_specs_by_name=self.role_specs_by_name,
            goal=self.configuration.goal,
        )

    def _initialize_run_components(
        self,
        max_parallel_workers: int,
        goal: str,
    ) -> None:
        self.run_id = self._resolve_run_id()
        self.runs_directory = Path(".runs") / self.run_id
        self._ensure_directory(self.runs_directory)
        self._run_store = RunStore(self.runs_directory)
        self._client_factory = RoleClientFactory(
            role_specs=self._agent_specs,
            runs_directory=self.runs_directory,
            ensure_directory=self._ensure_directory,
            max_instances_per_role=max_parallel_workers,
        )
        self._planner_client = self._create_planner_client()
        self._state_tracker = OrchestratorState(goal)
        self.state = self._state_tracker.state
        self._file_applier = FileApplier(
            ensure_directory=self._ensure_directory,
            write_text=self._write_text,
        )

    def _initialize_execution_counters(self) -> None:
        self._context = {}
        self._turn_counter = 0
        self._turn_lock = threading.Lock()
        self._wave_counter = 0
        self._planner_compact_artifact_relative = "planner_compact_input.md"

    def _initialize_manifest_state(self) -> None:
        resumed = self._restore_runtime_from_manifest()
        if resumed:
            self._record_manifest_event(
                "run_resumed",
                {
                    "run_id": self.run_id,
                    "restored_wave_counter": self._wave_counter,
                },
                idempotency_key=f"run_resumed:{self.run_id}",
            )
        else:
            self._run_store.write_artifact(
                self._planner_compact_artifact_relative,
                f"# Planner Compact Input\n\n{self.configuration.goal}\n",
            )
            self._record_manifest_event(
                "run_initialized",
                {
                    "run_id": self.run_id,
                    "agent_registry": self._agent_registry.to_dict(),
                },
                idempotency_key=f"run_initialized:{self.run_id}",
            )

    def _resolve_run_id(self) -> str:
        env_run_id = os.environ.get("RESUME_RUN_ID", "")
        if isinstance(env_run_id, str):
            stripped = env_run_id.strip()
            if stripped:
                run_id = stripped
            else:
                run_id = str(uuid.uuid4())[:8]
        else:
            run_id = str(uuid.uuid4())[:8]
        return run_id

    def _restore_runtime_from_manifest(self) -> bool:
        manifest_events = self._run_store.load_manifest()
        resumed = False
        if manifest_events:
            max_wave = 0
            for event in manifest_events:
                if isinstance(event, dict):
                    event_type = event.get("event_type")
                    payload = event.get("payload")
                    if event_type == "wave_completed" and isinstance(payload, dict):
                        wave_value = payload.get("wave")
                        if isinstance(wave_value, int) and wave_value > max_wave:
                            max_wave = wave_value
            if max_wave > 0:
                self._wave_counter = max_wave
            resumed = True
        return resumed

    def _find_planner_spec(
        self,
        role_specifications: List[RoleSpec],
    ) -> Optional[RoleSpec]:
        planner_spec: Optional[RoleSpec] = None
        for spec in role_specifications:
            if spec.behaviors.is_orchestrator or spec.name.lower() == "planner":
                planner_spec = spec
                break
        return planner_spec

    def _build_role_spec_index(
        self,
        role_specifications: List[RoleSpec],
    ) -> Dict[str, RoleSpec]:
        result = {spec.name: spec for spec in role_specifications}
        return result

    def _create_planner_client(self) -> RoleClient:
        client = CodexRoleClient(
            role_name=self._planner_spec.name,
            model=self._planner_spec.model,
            reasoning_effort=self._planner_spec.reasoning_effort,
        )
        role_events = self.runs_directory / self._planner_spec.name / "events.jsonl"
        self._ensure_directory(role_events.parent)
        client.events_file = role_events
        return client

    def _ensure_directory(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def _write_text(self, path: Union[Path, str], content: str) -> str:
        """Write text atomically under the run directory.

        Args:
            path: Absolute path or run-relative path.
            content: Text content to write.

        Returns:
            Absolute written path.
        """
        if isinstance(path, Path):
            target_path = path
        elif isinstance(path, str):
            if path.strip():
                target_path = self.runs_directory / path.strip()
            else:
                raise ValueError("path must not be empty")
        else:
            raise TypeError("path must be a Path or str")
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        self._ensure_directory(target_path.parent)
        temp_path = target_path.with_name(f"{target_path.name}.{uuid.uuid4().hex}.tmp")
        temp_path.write_text(content, encoding="utf-8")
        os.replace(temp_path, target_path)
        return str(target_path)

    def _next_turn_id(self) -> int:
        with self._turn_lock:
            self._turn_counter += 1
            turn_id = self._turn_counter
        return turn_id

    def start_all(self) -> None:
        """Start planner client."""
        self._logger.log("Starting Planner client...")
        self._planner_client.start()
        self._logger.log("Agent clients will spawn dynamically as needed.")

    def stop_all(self) -> None:
        """Stop planner and all dynamic worker clients."""
        self._logger.log("Stopping all clients...")
        try:
            self._planner_client.stop()
        except Exception as exc:
            self._logger.log(f"[warn] failed to stop planner: {exc}")
        self._client_factory.stop_all()

    def run(self) -> None:
        """Run planner-driven orchestration loop."""
        self._logger.log(f"Starting DynamicOrchestrator run: {self.run_id}")
        self._logger.log(f"Goal: {self.configuration.goal}")
        self.start_all()
        self._record_manifest_event(
            "run_started",
            {"goal": self.configuration.goal},
            idempotency_key=f"run_started:{self.run_id}",
        )
        try:
            self._context = self._build_initial_context()
            max_iterations = self.configuration.cycles * 10
            for iteration in range(max_iterations):
                self._logger.log(f"\n=== Iteration {iteration + 1} ===")
                decision = self._run_planner(self._context)
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
                    critical_questions = self._extract_critical_questions(decision.questions)
                    if critical_questions:
                        user_answers = self._handle_user_questions(critical_questions)
                        self._context = self._merge_user_answers(self._context, user_answers)
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
                    feedbacks: List[AgentFeedback] = []
                    for execution_result in results:
                        payload = execution_result.result or {}
                        if not execution_result.success and payload.get("error") is None:
                            payload = dict(payload)
                            payload["error"] = execution_result.error or "Unknown delegation error"
                        agent_name = getattr(execution_result, "agent", "unknown")
                        feedback = self._feedback_loop.process_agent_result(
                            agent_name,
                            execution_result.delegation_id,
                            payload,
                        )
                        feedbacks.append(feedback)
                        self._update_delegation_status_from_feedback(feedback)
                    pending_questions = self._feedback_loop.get_pending_clarifications(feedbacks)
                    if pending_questions:
                        self._logger.log(
                            f"Collected {len(pending_questions)} planner-facing question(s) from workers."
                        )
                        self._context = self._merge_pending_questions(
                            self._context,
                            pending_questions,
                        )
                    self._context = self._merge_delegation_results(self._context, results)
                    wave_duration = time.time() - wave_start
                    self._persist_wave_outputs(
                        decision=decision,
                        feedbacks=feedbacks,
                        results=results,
                        wave_duration_s=wave_duration,
                    )
                else:
                    self._context["iteration"] = self._context.get("iteration", 0) + 1
            else:
                self._logger.log("Maximum iterations reached. Stopping.")
        finally:
            self._persist_controller_state()
            self._record_manifest_event(
                "run_finished",
                {"state": "stopped"},
                idempotency_key=f"run_finished:{self.run_id}",
            )
            self.stop_all()
        self._logger.log(f"DynamicOrchestrator run completed: {self.run_id}")

    def _build_initial_context(self) -> Dict[str, Any]:
        context = {
            "goal": self.configuration.goal,
            "iteration": 0,
            "completed_delegations": [],
            "user_answers": {},
            "agent_results": {},
            "pending_questions": [],
            "answered_questions": self._load_answered_questions(),
            "active_assumptions": [],
            "planner_compact": str(
                (self._run_store.artifacts_directory / self._planner_compact_artifact_relative)
                .relative_to(self.runs_directory)
            ),
        }
        return context

    def _run_planner(self, context: Dict[str, Any]) -> PlannerDecision:
        turn_id = self._next_turn_id()
        planner_name = self._planner_spec.name
        prompt = self._prompt_builder._build_prompt(planner_name, context)
        timeout_s = self._timeout_resolver.resolve_timeout(
            self._planner_spec.behaviors.timeout_policy
        )
        self._logger.log(f"Running Planner (turn {turn_id})...")
        turn_result = self._planner_client.run_turn(prompt, timeout_s)
        self._persist_turn_artifacts(
            planner_name,
            turn_result.request_id,
            prompt,
            turn_result,
        )
        try:
            payload = self._json_formatter.parse_json_object_from_assistant_text(
                turn_result.assistant_text
            )
            self._state_tracker._update_state(planner_name, turn_result, payload)
            decision = PlannerDecision.from_payload(payload)
        except Exception as exc:
            self._logger.log(f"Failed to parse Planner JSON: {exc}")
            decision = PlannerDecision(
                summary="planner json parse error",
                action="delegate",
                status="CONTINUE",
                planner_decision={
                    "io_status": "NOT_OK",
                    "not_ok_reasons": [str(exc)],
                    "conflicts_resolved": [],
                    "next_actions": ["planner_retry"],
                },
            )
        return decision

    def _extract_critical_questions(self, questions: List[Question]) -> List[Question]:
        critical_questions = [question for question in questions if question.category == "critical"]
        return critical_questions

    def _handle_user_questions(
        self,
        questions: List[Question],
    ) -> Dict[str, Answer]:
        answer_map: Dict[str, Answer]
        if questions:
            self._user_interaction.notify(
                f"The Planner needs {len(questions)} critical answer(s) to proceed."
            )
            answers = self._user_interaction.ask_questions(questions)
            answer_map = {answer.question_id: answer for answer in answers}
        else:
            answer_map = {}
        return answer_map

    def _update_delegation_status_from_feedback(self, feedback: AgentFeedback) -> None:
        delegation = self._delegation_manager.get_delegation(feedback.delegation_id)
        if delegation is not None:
            if feedback.status == FeedbackStatus.COMPLETED:
                self._delegation_manager.update_delegation_status(
                    feedback.delegation_id,
                    DelegationStatus.COMPLETED,
                    result=feedback.result,
                )
            elif feedback.status == FeedbackStatus.BLOCKED:
                self._delegation_manager.update_delegation_status(
                    feedback.delegation_id,
                    DelegationStatus.BLOCKED,
                    result=feedback.result,
                    error=feedback.error or "blocked",
                )
            elif feedback.status == FeedbackStatus.NEEDS_CLARIFICATION:
                self._delegation_manager.update_delegation_status(
                    feedback.delegation_id,
                    DelegationStatus.NEEDS_CLARIFICATION,
                    result=feedback.result,
                )
            else:
                self._delegation_manager.update_delegation_status(
                    feedback.delegation_id,
                    DelegationStatus.FAILED,
                    result=feedback.result,
                    error=feedback.error or "failed",
                )

    def _merge_user_answers(
        self,
        context: Dict[str, Any],
        answers: Dict[str, Answer],
    ) -> Dict[str, Any]:
        context["user_answers"].update(
            {question_id: answer.answer for question_id, answer in answers.items()}
        )
        answered_records = context.get("answered_questions", [])
        if not isinstance(answered_records, list):
            answered_records = []
        for question_id, answer in answers.items():
            answer_record = {
                "question_id": question_id,
                "answer": answer.answer,
                "used_default": answer.used_default,
                "iteration": context.get("iteration", 0),
            }
            answered_records.append(answer_record)
            self._run_store.append_answer(
                redact_secrets(answer_record),
                idempotency_key=f"answer:{question_id}",
            )
            self._record_manifest_event(
                "user_answer",
                answer_record,
                idempotency_key=f"user_answer:{question_id}",
            )
        context["answered_questions"] = answered_records
        pending_questions = context.get("pending_questions", [])
        if isinstance(pending_questions, list):
            unresolved = [
                question_payload
                for question_payload in pending_questions
                if question_payload.get("id") not in answers
            ]
            context["pending_questions"] = unresolved
        return context

    def _execute_delegations(
        self,
        delegation_specs: List[Dict[str, Any]],
    ) -> List[ExecutionResult]:
        all_results: List[ExecutionResult] = []
        blocked_by_validation = False
        try:
            delegations = self._delegation_manager.create_delegations(delegation_specs)
        except Exception as exc:
            self._logger.log(f"Delegation validation failed: {exc}")
            all_results = self._build_failed_results_from_specs(delegation_specs, str(exc))
            blocked_by_validation = True

        if not blocked_by_validation:
            preflight_failures: List[ExecutionResult] = []
            executable_delegations: List[Delegation] = []
            for delegation in delegations:
                errors = self._agent_registry.validate_delegation(delegation)
                if errors:
                    error_text = "; ".join(errors)
                    delegation.mark_failed(error_text)
                    failed_result = ExecutionResult(
                        delegation_id=delegation.delegation_id,
                        success=False,
                        result={"error": error_text},
                        error=error_text,
                        duration_s=0.0,
                    )
                    setattr(failed_result, "agent", delegation.agent_id)
                    preflight_failures.append(failed_result)
                    self._record_manifest_event(
                        "delegation_preflight_failed",
                        {
                            "delegation_id": delegation.delegation_id,
                            "agent_id": delegation.agent_id,
                            "errors": errors,
                        },
                        idempotency_key=self._build_idempotency_key(
                            "delegation_preflight_failed",
                            {
                                "delegation_id": delegation.delegation_id,
                                "errors": errors,
                            },
                        ),
                    )
                else:
                    executable_delegations.append(delegation)
            all_results.extend(preflight_failures)
            if executable_delegations:
                waves = self._delegation_manager.get_execution_order(executable_delegations)
                for wave_index, wave in enumerate(waves):
                    self._logger.log(
                        f"  Executing wave {wave_index + 1}/{len(waves)} ({len(wave)} delegations)"
                    )
                    wave_results = self._parallel_executor.execute_parallel(
                        wave,
                        self._execute_agent,
                    )
                    for delegation in wave:
                        result = wave_results.get(delegation.delegation_id)
                        if result is None:
                            missing_result = ExecutionResult(
                                delegation_id=delegation.delegation_id,
                                success=False,
                                result={"error": "missing execution result"},
                                error="missing execution result",
                                duration_s=0.0,
                            )
                            setattr(missing_result, "agent", delegation.agent_id)
                            all_results.append(missing_result)
                        else:
                            normalized_result = ExecutionResult(
                                delegation_id=result.delegation_id,
                                success=result.success,
                                result=result.result,
                                error=result.error,
                                duration_s=result.duration_s,
                            )
                            setattr(normalized_result, "agent", delegation.agent_id)
                            all_results.append(normalized_result)
                            self._record_metric(
                                {
                                    "metric": "agent_latency",
                                    "agent_id": delegation.agent_id,
                                    "delegation_id": delegation.delegation_id,
                                    "value_s": result.duration_s,
                                },
                                idempotency_key=self._build_idempotency_key(
                                    "agent_latency",
                                    {
                                        "delegation_id": delegation.delegation_id,
                                        "duration_s": result.duration_s,
                                    },
                                ),
                            )
                            if delegation.agent_id == "implementer" and result.success:
                                self._apply_implementer_files(
                                    result.result or {},
                                    delegation.turn_directory,
                                )
        return all_results

    def _build_failed_results_from_specs(
        self,
        delegation_specs: List[Dict[str, Any]],
        error_text: str,
    ) -> List[ExecutionResult]:
        failed_results: List[ExecutionResult] = []
        for index, spec in enumerate(delegation_specs):
            delegation_id = f"invalid_{index}"
            agent_id = "unknown"
            if isinstance(spec, dict):
                raw_id = spec.get("delegation_id", spec.get("id"))
                raw_agent = spec.get("agent_id", spec.get("agent"))
                if isinstance(raw_id, str) and raw_id.strip():
                    delegation_id = raw_id
                if isinstance(raw_agent, str) and raw_agent.strip():
                    agent_id = raw_agent
            result = ExecutionResult(
                delegation_id=delegation_id,
                success=False,
                result={"error": error_text},
                error=error_text,
                duration_s=0.0,
            )
            setattr(result, "agent", agent_id)
            failed_results.append(result)
        if not failed_results:
            fallback_result = ExecutionResult(
                delegation_id="delegation_validation_error",
                success=False,
                result={"error": error_text},
                error=error_text,
                duration_s=0.0,
            )
            setattr(fallback_result, "agent", "unknown")
            failed_results.append(fallback_result)
        return failed_results

    def _execute_agent(self, delegation: Delegation) -> Dict[str, Any]:
        agent_name = delegation.agent_id
        turn_id = self._next_turn_id()
        client_instance = self._client_factory.acquire_client(
            agent_name,
            delegation.delegation_id,
        )
        self._logger.log(
            f"    Acquired client instance {client_instance.instance_id} for {agent_name}"
        )
        try:
            client_instance.client.start()
            context_packet = self._build_context_packet_for_delegation(delegation)
            agent_context = {
                "delegation_id": delegation.delegation_id,
                "agent_id": delegation.agent_id,
                "task_description": delegation.task_description,
                "acceptance_criteria": delegation.acceptance_criteria,
                "required_inputs": delegation.required_inputs,
                "provided_inputs": delegation.provided_inputs,
                "depends_on": delegation.depends_on,
                "context_packet": context_packet.to_dict(),
                "context": delegation.context,
            }
            prompt = self._prompt_builder._build_prompt(agent_name, agent_context)
            agent_spec = self._agent_specs.get(agent_name)
            timeout_policy = "default"
            if agent_spec is not None:
                timeout_policy = agent_spec.behaviors.timeout_policy
            timeout_s = self._timeout_resolver.resolve_timeout(timeout_policy)
            self._logger.log(
                f"    Running {agent_name} for delegation "
                f"{delegation.delegation_id} (turn {turn_id})..."
            )
            turn_result = client_instance.client.run_turn(prompt, timeout_s)
            delegation.turn_directory = f"{agent_name}/turn_{turn_result.request_id}"
            self._persist_turn_artifacts(
                agent_name,
                turn_result.request_id,
                prompt,
                turn_result,
            )
            try:
                payload = self._json_formatter.parse_json_object_from_assistant_text(
                    turn_result.assistant_text
                )
            except Exception as exc:
                self._logger.log(f"    Failed to parse {agent_name} JSON: {exc}")
                payload = {"error": str(exc), "status": "failed"}
            self._state_tracker._update_state(agent_name, turn_result, payload)
            self._persist_worker_payload(delegation, payload)
            result_payload = payload
        finally:
            self._client_factory.release_client(client_instance)
            self._logger.log(
                f"    Released client instance {client_instance.instance_id}"
            )
        return result_payload

    def _build_context_packet_for_delegation(self, delegation: Delegation) -> ContextPacket:
        pool_document = self._run_store.load_pool()
        facts_value = pool_document.get("facts", [])
        detail_index: List[DetailIndexEntry] = []
        if isinstance(facts_value, list):
            for fact in facts_value:
                if not isinstance(fact, dict):
                    continue
                if fact.get("superseded_by") is not None:
                    continue
                detail_id = fact.get("id")
                content = fact.get("content")
                if not isinstance(detail_id, str) or not detail_id.strip():
                    continue
                if not isinstance(content, str) or not content.strip():
                    continue
                title = fact.get("origin", "detail")
                if not isinstance(title, str) or not title.strip():
                    title = "detail"
                tags = fact.get("source_refs", [])
                if not isinstance(tags, list):
                    tags = []
                summary = content[:280]
                detail_index.append(
                    DetailIndexEntry(
                        id=detail_id,
                        title=title,
                        summary=summary,
                        tags=[str(tag) for tag in tags],
                    )
                )
                if len(detail_index) >= 32:
                    break
        answered_questions = self._load_answered_questions()
        active_assumptions = self._get_active_assumptions(pool_document)
        planner_compact_path = self._run_store.artifacts_directory / self._planner_compact_artifact_relative
        planner_compact_relative = str(planner_compact_path.relative_to(self.runs_directory))
        context_packet = ContextPacket(
            planner_compact=planner_compact_relative,
            detail_index=detail_index,
            answered_questions=answered_questions,
            active_assumptions=active_assumptions,
        )
        return context_packet

    def _persist_worker_payload(
        self,
        delegation: Delegation,
        payload: Dict[str, Any],
    ) -> None:
        redacted_payload = redact_secrets(payload)
        inbox_record = {
            "delegation_id": delegation.delegation_id,
            "agent_id": delegation.agent_id,
            "payload": redacted_payload,
        }
        self._run_store.append_inbox(
            inbox_record,
            idempotency_key=self._build_idempotency_key(
                "inbox",
                {"delegation_id": delegation.delegation_id, "payload": redacted_payload},
            ),
        )
        self._record_manifest_event(
            "worker_output",
            inbox_record,
            idempotency_key=self._build_idempotency_key(
                "worker_output",
                {"delegation_id": delegation.delegation_id, "payload": redacted_payload},
            ),
        )
        raw_side_effect_log = payload.get("side_effect_log")
        if isinstance(raw_side_effect_log, list):
            normalized_side_effect_log: List[Dict[str, Any]] = []
            for side_effect_entry in raw_side_effect_log:
                if isinstance(side_effect_entry, dict):
                    normalized_side_effect_log.append(side_effect_entry)
                else:
                    normalized_side_effect_log.append(
                        {"event": str(side_effect_entry)}
                    )
            if normalized_side_effect_log:
                self._record_manifest_event(
                    "worker_side_effect_log",
                    {
                        "delegation_id": delegation.delegation_id,
                        "agent_id": delegation.agent_id,
                        "side_effect_log": redact_secrets(normalized_side_effect_log),
                    },
                    idempotency_key=self._build_idempotency_key(
                        "worker_side_effect_log",
                        {
                            "delegation_id": delegation.delegation_id,
                            "side_effect_log": normalized_side_effect_log,
                        },
                    ),
                )

    def _load_answered_questions(self) -> List[Dict[str, Any]]:
        answer_records = self._run_store.load_answers()
        normalized_answers: List[Dict[str, Any]] = []
        for answer_record in answer_records:
            if isinstance(answer_record, dict):
                question_id = answer_record.get("question_id")
                answer_value = answer_record.get("answer")
                if isinstance(question_id, str) and isinstance(answer_value, str):
                    normalized_answers.append(
                        {
                            "question_id": question_id,
                            "answer": answer_value,
                        }
                    )
        return normalized_answers

    def _get_active_assumptions(self, pool_document: Dict[str, Any]) -> List[str]:
        assumptions: List[str] = []
        facts = pool_document.get("facts", [])
        if isinstance(facts, list):
            for fact in facts:
                if not isinstance(fact, dict):
                    continue
                is_assumption = bool(fact.get("is_assumption", False))
                not_superseded = fact.get("superseded_by") is None
                content_value = fact.get("content")
                if is_assumption and not_superseded and isinstance(content_value, str):
                    assumptions.append(content_value)
        return assumptions

    def _apply_implementer_files(
        self,
        payload: Dict[str, Any],
        turn_directory: Optional[str],
    ) -> None:
        files_value = payload.get("files", [])
        if isinstance(files_value, list) and files_value:
            if turn_directory:
                self._logger.log(f"    Applying {len(files_value)} files from Implementer...")
                self._file_applier._apply_implementer_files(
                    payload,
                    turn_directory,
                )
            else:
                self._logger.log("    Skipping implementer files: missing turn directory.")

    def _merge_pending_questions(
        self,
        context: Dict[str, Any],
        questions: List[Question],
    ) -> Dict[str, Any]:
        pending_questions = context.get("pending_questions", [])
        if not isinstance(pending_questions, list):
            pending_questions = []
        existing_ids = {
            question_payload.get("id")
            for question_payload in pending_questions
            if isinstance(question_payload, dict)
        }
        for question in questions:
            if question.id not in existing_ids:
                pending_questions.append(
                    {
                        "id": question.id,
                        "question": question.question,
                        "category": question.category,
                        "context": question.context,
                        "priority": question.priority,
                        "expected_answer_format": question.expected_answer_format,
                    }
                )
                existing_ids.add(question.id)
        context["pending_questions"] = pending_questions
        return context

    def _merge_delegation_results(
        self,
        context: Dict[str, Any],
        results: List[ExecutionResult],
    ) -> Dict[str, Any]:
        completed_ids = context.get("completed_delegations", [])
        if not isinstance(completed_ids, list):
            completed_ids = []
        agent_results = context.get("agent_results", {})
        if not isinstance(agent_results, dict):
            agent_results = {}
        for result in results:
            if result.success:
                completed_ids.append(result.delegation_id)
                agent_results[result.delegation_id] = result.result
        context["completed_delegations"] = completed_ids
        context["agent_results"] = agent_results
        context["answered_questions"] = self._load_answered_questions()
        context["active_assumptions"] = self._get_active_assumptions(
            self._run_store.load_pool()
        )
        context["iteration"] = context.get("iteration", 0) + 1
        return context

    def _persist_wave_outputs(
        self,
        decision: PlannerDecision,
        feedbacks: List[AgentFeedback],
        results: List[ExecutionResult],
        wave_duration_s: float,
    ) -> None:
        self._wave_counter += 1
        wave_index = self._wave_counter
        compact_path, detailed_path = self._write_wave_documents(
            wave_index=wave_index,
            decision=decision,
            feedbacks=feedbacks,
            results=results,
        )
        self._context["planner_compact"] = str(
            compact_path.relative_to(self.runs_directory)
        )
        planner_decision_payload = self._derive_planner_decision_payload(
            decision,
            results,
        )
        planner_decision_payload = self._merge_conflicts_into_planner_decision(
            planner_decision_payload,
            feedbacks,
        )
        pool_entries = self._build_pool_entries(feedbacks, wave_index)
        updated_pool = self._run_store.merge_pool_entries(pool_entries)
        self._record_wave_metrics(
            wave_index=wave_index,
            results=results,
            planner_decision_payload=planner_decision_payload,
            wave_duration_s=wave_duration_s,
        )
        self._record_manifest_event(
            "wave_completed",
            {
                "wave": wave_index,
                "compact_path": str(compact_path.relative_to(self.runs_directory)),
                "detailed_path": str(detailed_path.relative_to(self.runs_directory)),
                "planner_decision": planner_decision_payload,
                "pool_size": len(updated_pool.get("facts", [])),
            },
            idempotency_key=self._build_idempotency_key(
                "wave_completed",
                {"wave": wave_index},
            ),
        )

    def _write_wave_documents(
        self,
        wave_index: int,
        decision: PlannerDecision,
        feedbacks: List[AgentFeedback],
        results: List[ExecutionResult],
    ) -> tuple[Path, Path]:
        wave_compact_md = self._build_wave_compact(decision, feedbacks, results)
        wave_detailed_md = self._build_wave_detailed(decision, feedbacks, results)
        compact_path, detailed_path = self._run_store.write_wave_documents(
            wave_index,
            wave_compact_md,
            wave_detailed_md,
        )
        self._run_store.write_artifact(
            self._planner_compact_artifact_relative,
            wave_compact_md,
        )
        return compact_path, detailed_path

    def _merge_conflicts_into_planner_decision(
        self,
        planner_decision_payload: Dict[str, Any],
        feedbacks: List[AgentFeedback],
    ) -> Dict[str, Any]:
        merged_payload = dict(planner_decision_payload)
        detected_conflicts = self._detect_feedback_conflicts(feedbacks)
        existing_conflicts = merged_payload.get("conflicts_resolved", [])
        if isinstance(existing_conflicts, list):
            merged_conflicts = list(existing_conflicts)
        else:
            merged_conflicts = []
        for conflict in detected_conflicts:
            if conflict not in merged_conflicts:
                merged_conflicts.append(conflict)
        merged_payload["conflicts_resolved"] = merged_conflicts
        if detected_conflicts:
            io_status_value = merged_payload.get("io_status")
            if not isinstance(io_status_value, str) or io_status_value.upper() == "OK":
                merged_payload["io_status"] = "NOT_OK"
            reasons_value = merged_payload.get("not_ok_reasons", [])
            if isinstance(reasons_value, list):
                not_ok_reasons = list(reasons_value)
            else:
                not_ok_reasons = []
            for conflict in detected_conflicts:
                conflict_reason = f"conflict_detected: {conflict}"
                if conflict_reason not in not_ok_reasons:
                    not_ok_reasons.append(conflict_reason)
            merged_payload["not_ok_reasons"] = not_ok_reasons
        return merged_payload

    def _record_wave_metrics(
        self,
        wave_index: int,
        results: List[ExecutionResult],
        planner_decision_payload: Dict[str, Any],
        wave_duration_s: float,
    ) -> None:
        successful_results = [result for result in results if result.success]
        success_rate = 0.0
        if results:
            success_rate = len(successful_results) / len(results)
        self._record_metric(
            {
                "metric": "wave_time",
                "wave": wave_index,
                "value_s": wave_duration_s,
            },
            idempotency_key=self._build_idempotency_key(
                "wave_time",
                {"wave": wave_index, "value_s": wave_duration_s},
            ),
        )
        self._record_metric(
            {
                "metric": "wave_duration",
                "wave": wave_index,
                "value_s": wave_duration_s,
            },
            idempotency_key=self._build_idempotency_key(
                "wave_duration",
                {"wave": wave_index, "value_s": wave_duration_s},
            ),
        )
        self._record_metric(
            {
                "metric": "agent_success_rate",
                "wave": wave_index,
                "value": success_rate,
            },
            idempotency_key=self._build_idempotency_key(
                "agent_success_rate",
                {"wave": wave_index, "value": success_rate},
            ),
        )
        if planner_decision_payload.get("io_status") == "NOT_OK":
            self._record_metric(
                {
                    "metric": "not_ok_reasons",
                    "wave": wave_index,
                    "reasons": planner_decision_payload.get("not_ok_reasons", []),
                },
                idempotency_key=self._build_idempotency_key(
                    "not_ok_reasons",
                    {
                        "wave": wave_index,
                        "reasons": planner_decision_payload.get("not_ok_reasons", []),
                    },
                ),
            )
            self._record_metric(
                {
                    "metric": "failure_reasons",
                    "wave": wave_index,
                    "reasons": planner_decision_payload.get("not_ok_reasons", []),
                },
                idempotency_key=self._build_idempotency_key(
                    "failure_reasons",
                    {
                        "wave": wave_index,
                        "reasons": planner_decision_payload.get("not_ok_reasons", []),
                    },
                ),
            )

    def _build_wave_compact(
        self,
        decision: PlannerDecision,
        feedbacks: List[AgentFeedback],
        results: List[ExecutionResult],
    ) -> str:
        compact_text: str
        if decision.wave_compact_md.strip():
            compact_text = decision.wave_compact_md
        else:
            summary_lines = [
                f"# Wave {self._wave_counter} Compact",
                "",
                f"Planner summary: {decision.summary or 'No planner summary provided.'}",
                "",
                "## Delegation Results",
            ]
            for result in results:
                agent_name = getattr(result, "agent", "unknown")
                status = "ok" if result.success else "failed"
                summary_lines.append(f"- `{result.delegation_id}` ({agent_name}): {status}")
            blocked_feedbacks = [feedback for feedback in feedbacks if feedback.is_blocked]
            if blocked_feedbacks:
                summary_lines.append("")
                summary_lines.append("## Blocked")
                for feedback in blocked_feedbacks:
                    summary_lines.append(
                        f"- `{feedback.delegation_id}`: {'; '.join(feedback.blockers) or 'blocked'}"
                    )
            compact_text = "\n".join(summary_lines) + "\n"
        return compact_text

    def _build_wave_detailed(
        self,
        decision: PlannerDecision,
        feedbacks: List[AgentFeedback],
        results: List[ExecutionResult],
    ) -> str:
        detailed_text: str
        if decision.wave_detailed_md.strip():
            detailed_text = decision.wave_detailed_md
        else:
            lines = [
                f"# Wave {self._wave_counter} Detailed",
                "",
                "## Planner Summary",
                decision.summary or "No planner summary provided.",
                "",
                "## Delegation Outcomes",
            ]
            for result in results:
                agent_name = getattr(result, "agent", "unknown")
                lines.append(f"### {result.delegation_id} ({agent_name})")
                lines.append(f"- success: {result.success}")
                lines.append(f"- duration_s: {result.duration_s}")
                if result.error:
                    lines.append(f"- error: {result.error}")
                payload = result.result or {}
                lines.append("- payload:")
                lines.append("```json")
                lines.append(json.dumps(redact_secrets(payload), ensure_ascii=False, indent=2))
                lines.append("```")
            if feedbacks:
                lines.append("")
                lines.append("## Feedback Summary")
                feedback_summary = self._feedback_loop.get_feedback_summary()
                lines.append("```json")
                lines.append(
                    json.dumps(redact_secrets(feedback_summary), ensure_ascii=False, indent=2)
                )
                lines.append("```")
            detailed_text = "\n".join(lines) + "\n"
        return detailed_text

    def _detect_feedback_conflicts(self, feedbacks: List[AgentFeedback]) -> List[str]:
        criteria_resolution: Dict[str, Dict[str, List[str]]] = {}
        for feedback in feedbacks:
            worker_output = feedback.worker_output
            if worker_output is None:
                continue
            met = worker_output.coverage.get("criteria_met", [])
            unmet = worker_output.coverage.get("criteria_unmet", [])
            for criterion in met:
                criterion_bucket = criteria_resolution.setdefault(
                    criterion,
                    {"met": [], "unmet": []},
                )
                criterion_bucket["met"].append(feedback.delegation_id)
            for criterion in unmet:
                criterion_bucket = criteria_resolution.setdefault(
                    criterion,
                    {"met": [], "unmet": []},
                )
                criterion_bucket["unmet"].append(feedback.delegation_id)
        conflicts: List[str] = []
        for criterion, resolution in criteria_resolution.items():
            if resolution["met"] and resolution["unmet"]:
                conflicts.append(
                    (
                        f"criterion '{criterion}' met by {resolution['met']} "
                        f"but unmet by {resolution['unmet']}"
                    )
                )
        return conflicts

    def _derive_planner_decision_payload(
        self,
        decision: PlannerDecision,
        results: List[ExecutionResult],
    ) -> Dict[str, Any]:
        planner_decision_payload = dict(decision.planner_decision)
        if "io_status" not in planner_decision_payload:
            failed_results = [result for result in results if not result.success]
            if failed_results:
                io_status = "NOT_OK"
                not_ok_reasons = [
                    f"{result.delegation_id}: {result.error or 'unknown failure'}"
                    for result in failed_results
                ]
            else:
                io_status = "OK"
                not_ok_reasons = []
            planner_decision_payload = {
                "io_status": io_status,
                "not_ok_reasons": not_ok_reasons,
                "conflicts_resolved": [],
                "next_actions": [],
            }
        if "not_ok_reasons" not in planner_decision_payload:
            planner_decision_payload["not_ok_reasons"] = []
        if "conflicts_resolved" not in planner_decision_payload:
            planner_decision_payload["conflicts_resolved"] = []
        if "next_actions" not in planner_decision_payload:
            planner_decision_payload["next_actions"] = []
        return planner_decision_payload

    def _build_pool_entries(
        self,
        feedbacks: List[AgentFeedback],
        wave_index: int,
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        wave_ref = f"wave_{wave_index:02d}"
        for feedback in feedbacks:
            worker_output = feedback.worker_output
            if worker_output is None:
                continue
            compact_content = worker_output.compact_md.strip()
            if compact_content:
                confidence = 0.8
                if feedback.status.value == "failed":
                    confidence = 0.3
                elif feedback.status.value == "blocked":
                    confidence = 0.5
                entries.append(
                    {
                        "id": f"fact_{wave_index:02d}_{feedback.delegation_id}",
                        "content": compact_content,
                        "origin": "delegation",
                        "confidence": confidence,
                        "is_assumption": False,
                        "source_refs": [wave_ref, feedback.delegation_id],
                        "superseded_by": None,
                    }
                )
            for assumption_index, assumption in enumerate(worker_output.assumptions_made):
                entries.append(
                    {
                        "id": (
                            f"fact_{wave_index:02d}_{feedback.delegation_id}"
                            f"_assumption_{assumption_index}"
                        ),
                        "content": assumption,
                        "origin": "delegation",
                        "confidence": 0.4,
                        "is_assumption": True,
                        "source_refs": [wave_ref, feedback.delegation_id],
                        "superseded_by": None,
                    }
                )
        return entries

    def _build_idempotency_key(self, prefix: str, payload: Dict[str, Any]) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
        return f"{prefix}:{digest}"

    def _record_manifest_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> None:
        redacted_payload = redact_secrets(payload)
        self._run_store.append_manifest(
            event_type=event_type,
            payload=redacted_payload,
            idempotency_key=idempotency_key,
        )

    def _record_metric(
        self,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> None:
        redacted_payload = redact_secrets(payload)
        self._run_store.append_metric(
            redacted_payload,
            idempotency_key=idempotency_key,
        )

    def _persist_turn_artifacts(
        self,
        role_name: str,
        turn_id: Union[int, str],
        prompt: str,
        turn_result: TurnResult,
    ) -> None:
        turn_dir = self.runs_directory / role_name / f"turn_{turn_id}"
        self._ensure_directory(turn_dir)
        self._write_text(turn_dir / "prompt.txt", prompt)
        self._write_text(
            turn_dir / "assistant_text.txt",
            turn_result.assistant_text or "",
        )
        if turn_result.full_items_text:
            self._write_text(turn_dir / "items_text.md", turn_result.full_items_text)

    def _persist_controller_state(self) -> None:
        state_path = self.runs_directory / "controller_state.json"
        state_json = json.dumps(self.state, indent=2, ensure_ascii=False)
        self._write_text(state_path, state_json)
        self._logger.log(f"State persisted to {state_path}")
