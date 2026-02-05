"""Dynamic orchestrator implementation.

This module implements the dynamic orchestration architecture where the Planner
acts as the central decision-making hub, dynamically delegating to other agents,
handling user interaction, and processing feedback.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..roles.role_client import RoleClient
from ..client.codex_role_client import CodexRoleClient
from ..utils.env_utils import EnvironmentReader
from ..utils.json_utils import JsonPayloadFormatter
from ..logging import TimestampLogger
from ..sequential.orchestrator_config import OrchestratorConfig
from ..roles.role_spec import RoleSpec, RoleSpecCatalog
from ..prompt_builder import PromptBuilder
from ..timeout_resolver import TimeoutResolver
from ..turn_result import TurnResult
from ..sequential.orchestrator_state import OrchestratorState
from ..sequential.file_applier import FileApplier
from .user_interaction import (
    Answer,
    ConsoleUserInteraction,
    Question,
    UserInteraction,
)
from .delegation_manager import (
    Delegation,
    DelegationManager,
    DelegationStatus,
)
from .feedback_loop import AgentFeedback, FeedbackLoop, FeedbackStatus
from .parallel_executor import ExecutionResult, ParallelExecutor, WaveResult
from .role_client_factory import RoleClientFactory, ClientInstance


ANALYSIS_KEY = "analysis_md"
ANALYSIS_PATH_KEY = "analysis_md_path"


@dataclass
class PlannerDecision:
    """Represents a decision made by the Planner.

    Attributes:
        summary: Brief summary of the decision.
        needs_user_input: Whether user clarification is needed.
        questions: Questions to ask the user.
        delegations: Agent delegations to execute.
        action: The action to take (delegate, ask_user, done).
        status: Overall status (CONTINUE or DONE).
        raw_payload: The complete raw JSON payload from Planner.
    """

    summary: str = ""
    needs_user_input: bool = False
    questions: List[Question] = field(default_factory=list)
    delegations: List[Dict[str, Any]] = field(default_factory=list)
    action: str = "delegate"
    status: str = "CONTINUE"
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "PlannerDecision":
        """Create a PlannerDecision from a Planner's JSON output.

        Args:
            payload: The parsed JSON payload from the Planner.

        Returns:
            PlannerDecision instance.
        """
        questions = []
        raw_questions = payload.get("questions", [])
        for q in raw_questions:
            questions.append(
                Question(
                    id=q.get("id", str(uuid.uuid4())),
                    question=q.get("question", ""),
                    category=q.get("category", "optional"),
                    default_suggestion=q.get("default_suggestion"),
                    context=q.get("context"),
                )
            )

        return cls(
            summary=payload.get("summary", ""),
            needs_user_input=payload.get("needs_user_input", False),
            questions=questions,
            delegations=payload.get("delegations", []),
            action=payload.get("action", "delegate"),
            status=payload.get("status", "CONTINUE"),
            raw_payload=payload,
        )

    @property
    def is_done(self) -> bool:
        """Check if the Planner signaled completion."""
        return self.status == "DONE" or self.action == "done"


class DynamicOrchestrator:
    """Dynamic orchestrator where Planner acts as the decision-making hub.

    Unlike SequentialRunner which runs roles in fixed sequence,
    this orchestrator lets the Planner dynamically decide:
    - Which agents to run
    - In what order (including parallel)
    - When to ask the user for input
    - How to handle agent feedback
    """

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
        """Initialize the Planner orchestrator.

        Args:
            role_specifications: Ordered list of RoleSpec objects.
            configuration: Orchestrator configuration values.
            user_interaction: Interface for user communication.
            environment_reader: Optional reader for environment values.
            json_formatter: Optional JSON formatter for payload serialization.
            logger: Optional logger for run output.
            role_spec_catalog: Optional catalog for prompt formatting.
            max_parallel_workers: Maximum parallel agent executions.
        """
        # Resolve dependencies
        self._environment_reader = environment_reader or EnvironmentReader()
        self._json_formatter = json_formatter or JsonPayloadFormatter()
        self._logger = logger or TimestampLogger()
        self._role_spec_catalog = role_spec_catalog or RoleSpecCatalog(
            environment_reader=self._environment_reader
        )
        self._user_interaction = user_interaction or ConsoleUserInteraction()

        self.configuration = configuration
        self.role_specs_by_name = self._build_role_spec_index(role_specifications)
        self._prompt_builder = PromptBuilder(
            role_spec_catalog=self._role_spec_catalog,
            json_formatter=self._json_formatter,
            role_specs_by_name=self.role_specs_by_name,
            goal=self.configuration.goal,
        )
        self._timeout_resolver = TimeoutResolver(self._environment_reader)

        # Find the Planner role
        self._planner_spec = self._find_planner_spec(role_specifications)
        if not self._planner_spec:
            raise ValueError("No Planner role found in role_specifications")

        # Build agent specs (non-planner roles)
        self._agent_specs = {
            name: spec
            for name, spec in self.role_specs_by_name.items()
            if name != self._planner_spec.name
        }

        # Initialize components
        self._delegation_manager = DelegationManager(
            available_agents=set(self._agent_specs.keys())
        )
        self._feedback_loop = FeedbackLoop(user_interaction=self._user_interaction)
        self._parallel_executor = ParallelExecutor(max_workers=max_parallel_workers)

        # Setup run directory
        self.run_id = str(uuid.uuid4())[:8]
        self.runs_directory = Path(".runs") / self.run_id
        self._ensure_directory(self.runs_directory)

        # Build role client factory for dynamic spawning of agent instances
        self._client_factory = RoleClientFactory(
            role_specs=self._agent_specs,
            runs_directory=self.runs_directory,
            ensure_directory=self._ensure_directory,
            max_instances_per_role=max_parallel_workers,
        )

        # Single client for Planner (doesn't need multi-instance)
        self._planner_client = self._create_planner_client()

        # State tracking
        self._state_tracker = OrchestratorState(configuration.goal)
        self.state = self._state_tracker.state
        self._file_applier = FileApplier(
            ensure_directory=self._ensure_directory,
            write_text=self._write_text,
        )

        # Execution context
        self._context: Dict[str, Any] = {}
        self._turn_counter = 0
        self._turn_lock = threading.Lock()

    def _find_planner_spec(
        self,
        role_specifications: List[RoleSpec],
    ) -> Optional[RoleSpec]:
        """Find the Planner role specification.

        Args:
            role_specifications: List of role specifications.

        Returns:
            The Planner RoleSpec, or None if not found.
        """
        for spec in role_specifications:
            if spec.behaviors.is_orchestrator or spec.name.lower() == "planner":
                return spec
        return None

    def _build_role_spec_index(
        self,
        role_specifications: List[RoleSpec],
    ) -> Dict[str, RoleSpec]:
        """Index role specifications by name."""
        return {spec.name: spec for spec in role_specifications}

    def _create_planner_client(self) -> RoleClient:
        """Create the single Planner client instance."""
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
        """Ensure a directory exists."""
        path.mkdir(parents=True, exist_ok=True)

    def _write_text(self, path: Union[Path, str], content: str) -> str:
        """Write text content to a file and return the path."""
        if isinstance(path, Path):
            target_path = path
        elif isinstance(path, str):
            if path.strip():
                target_path = self.runs_directory / path.strip()
            else:
                raise ValueError("path must not be empty")
        else:
            raise TypeError("path must be a Path or str")

        if isinstance(content, str):
            text_content = content
        else:
            raise TypeError("content must be a string")

        self._ensure_directory(target_path.parent)
        target_path.write_text(text_content, encoding="utf-8")
        result = str(target_path)
        return result

    def _next_turn_id(self) -> int:
        """Allocate the next turn id in a thread-safe way."""
        with self._turn_lock:
            self._turn_counter += 1
            turn_id = self._turn_counter
        return turn_id

    def start_all(self) -> None:
        """Start Planner client (agent clients spawn dynamically)."""
        self._logger.log("Starting Planner client...")
        self._planner_client.start()
        self._logger.log("Agent clients will spawn dynamically as needed.")

    def stop_all(self) -> None:
        """Stop Planner and all spawned agent clients."""
        self._logger.log("Stopping all clients...")
        self._logger.log("  Stopping Planner...")
        self._planner_client.stop()
        self._logger.log("  Stopping agent client factory...")
        self._client_factory.stop_all()

    def run(self) -> None:
        """Main orchestration loop driven by Planner decisions."""
        self._logger.log(f"Starting DynamicOrchestrator run: {self.run_id}")
        self._logger.log(f"Goal: {self.configuration.goal}")

        self.start_all()

        try:
            self._context = self._build_initial_context()
            max_iterations = self.configuration.cycles * 10  # Safety limit

            for iteration in range(max_iterations):
                self._logger.log(f"\n=== Iteration {iteration + 1} ===")

                # 1. Run Planner to get next decision
                decision = self._run_planner(self._context)
                self._logger.log(f"Planner decision: action={decision.action}, status={decision.status}")

                # 2. Check for completion
                if decision.is_done:
                    self._logger.log("Planner signaled DONE. Completing run.")
                    break

                # 3. Handle user interaction if needed
                if decision.needs_user_input:
                    self._logger.log(f"Planner needs user input ({len(decision.questions)} questions)")
                    user_answers = self._handle_user_questions(decision.questions)
                    self._context = self._merge_user_answers(self._context, user_answers)
                    continue

                # 4. Execute delegations
                if decision.delegations:
                    self._logger.log(f"Executing {len(decision.delegations)} delegations")
                    results = self._execute_delegations(decision.delegations)

                    # 5. Handle agent feedback/clarifications
                    feedbacks: List[AgentFeedback] = []
                    for result in results:
                        payload = result.result or {}
                        if not result.success:
                            if payload.get("error") is None:
                                payload = dict(payload)
                                payload["error"] = result.error or "Unknown delegation error"
                        feedbacks.append(
                            self._feedback_loop.process_agent_result(
                                result.agent,
                                result.delegation_id,
                                payload,
                            )
                        )

                    if self._has_clarification_requests(feedbacks):
                        self._logger.log("Agents need clarification")
                        clarifications = self._handle_agent_clarifications(feedbacks)
                        self._context = self._merge_clarifications(
                            self._context, clarifications
                        )

                    # 6. Update context with results
                    self._context = self._merge_delegation_results(
                        self._context, results
                    )

            else:
                self._logger.log("Maximum iterations reached. Stopping.")

        finally:
            self._persist_controller_state()
            self.stop_all()

        self._logger.log(f"DynamicOrchestrator run completed: {self.run_id}")

    def _build_initial_context(self) -> Dict[str, Any]:
        """Build the initial context for the orchestration.

        Returns:
            Initial context dictionary.
        """
        return {
            "goal": self.configuration.goal,
            "iteration": 0,
            "completed_delegations": [],
            "user_answers": {},
            "agent_results": {},
            "clarifications": {},
        }

    def _run_planner(self, context: Dict[str, Any]) -> PlannerDecision:
        """Run the Planner to get the next decision.

        Args:
            context: Current orchestration context.

        Returns:
            PlannerDecision with the Planner's decision.
        """
        turn_id = self._next_turn_id()
        planner_name = self._planner_spec.name

        # Build prompt with context
        prompt = self._prompt_builder._build_prompt(
            planner_name,
            context,
        )

        # Get timeout
        timeout_s = self._timeout_resolver.resolve_timeout(
            self._planner_spec.behaviors.timeout_policy
        )

        # Run Planner turn
        self._logger.log(f"Running Planner (turn {turn_id})...")
        turn_result = self._planner_client.run_turn(prompt, timeout_s)

        # Persist turn artifacts
        self._persist_turn_artifacts(
            planner_name,
            turn_result.request_id,
            prompt,
            turn_result,
        )

        # Parse JSON response
        result: PlannerDecision
        try:
            payload = self._json_formatter.parse_json_object_from_assistant_text(
                turn_result.assistant_text
            )
            # Update state
            self._state_tracker._update_state(planner_name, turn_result, payload)
            result = PlannerDecision.from_payload(payload)
        except Exception as exc:
            self._logger.log(f"Failed to parse Planner JSON: {exc}")
            # Return a default decision to continue
            result = PlannerDecision(
                summary="JSON parse error",
                action="delegate",
                status="CONTINUE",
            )

        return result

    def _handle_user_questions(
        self,
        questions: List[Question],
    ) -> Dict[str, Answer]:
        """Handle user questions from the Planner.

        Args:
            questions: Questions to ask the user.

        Returns:
            Dict mapping question IDs to answers.
        """
        if not questions:
            return {}

        self._user_interaction.notify(
            f"The Planner needs {len(questions)} answer(s) to proceed."
        )
        answers = self._user_interaction.ask_questions(questions)

        return {a.question_id: a for a in answers}

    def _merge_user_answers(
        self,
        context: Dict[str, Any],
        answers: Dict[str, Answer],
    ) -> Dict[str, Any]:
        """Merge user answers into the context.

        Args:
            context: Current context.
            answers: User answers to merge.

        Returns:
            Updated context.
        """
        context["user_answers"].update(
            {q_id: ans.answer for q_id, ans in answers.items()}
        )
        return context

    def _execute_delegations(
        self,
        delegation_specs: List[Dict[str, Any]],
    ) -> List[ExecutionResult]:
        """Execute agent delegations.

        Args:
            delegation_specs: List of delegation specifications from Planner.

        Returns:
            List of execution results.
        """
        # Create delegation objects
        delegations = self._delegation_manager.create_delegations(delegation_specs)

        # Get execution order (waves)
        waves = self._delegation_manager.get_execution_order(delegations)

        # Execute waves
        all_results: List[ExecutionResult] = []

        for wave_idx, wave in enumerate(waves):
            self._logger.log(f"  Executing wave {wave_idx + 1}/{len(waves)} ({len(wave)} delegations)")

            # Execute wave in parallel
            wave_results = self._parallel_executor.execute_parallel(
                wave,
                self._execute_agent,
            )

            # Collect results
            for delegation in wave:
                result = wave_results.get(delegation.id)
                if result:
                    # Add agent info to result
                    result_with_agent = ExecutionResult(
                        delegation_id=result.delegation_id,
                        success=result.success,
                        result=result.result,
                        error=result.error,
                        duration_s=result.duration_s,
                    )
                    # Attach agent name for feedback processing
                    setattr(result_with_agent, "agent", delegation.agent)
                    all_results.append(result_with_agent)

                    # Apply files if implementer
                    if delegation.agent == "implementer" and result.success:
                        self._apply_implementer_files(
                            result.result or {},
                            delegation.turn_directory,
                        )

        return all_results

    def _execute_agent(self, delegation: Delegation) -> Dict[str, Any]:
        """Execute a single agent delegation using dynamically spawned client.

        Args:
            delegation: The delegation to execute.

        Returns:
            The agent's result payload.
        """
        agent_name = delegation.agent
        turn_id = self._next_turn_id()

        # Acquire a client instance from the factory
        client_instance = self._client_factory.acquire_client(agent_name, delegation.id)
        self._logger.log(
            f"    Acquired client instance {client_instance.instance_id} for {agent_name}"
        )

        try:
            # Start the client if not already running
            client_instance.client.start()

            # Build prompt for agent
            agent_context = {
                "task": delegation.task,
                "context": delegation.context,
                "delegation_id": delegation.id,
            }
            prompt = self._prompt_builder._build_prompt(agent_name, agent_context)

            # Get timeout
            agent_spec = self._agent_specs.get(agent_name)
            timeout_policy = agent_spec.behaviors.timeout_policy if agent_spec else "default"
            timeout_s = self._timeout_resolver.resolve_timeout(timeout_policy)

            # Run agent turn
            self._logger.log(
                f"    Running {agent_name} for delegation {delegation.id} (turn {turn_id})..."
            )
            turn_result = client_instance.client.run_turn(prompt, timeout_s)
            delegation.turn_directory = f"{agent_name}/turn_{turn_result.request_id}"

            # Persist turn artifacts
            self._persist_turn_artifacts(
                agent_name,
                turn_result.request_id,
                prompt,
                turn_result,
            )

            # Parse JSON response
            try:
                payload = self._json_formatter.parse_json_object_from_assistant_text(
                    turn_result.assistant_text
                )
            except Exception as e:
                self._logger.log(f"    Failed to parse {agent_name} JSON: {e}")
                payload = {"error": str(e), "needs_clarification": False}

            # Update state
            self._state_tracker._update_state(agent_name, turn_result, payload)
            result = payload

        finally:
            # Release the client back to the factory
            self._client_factory.release_client(client_instance)
            self._logger.log(
                f"    Released client instance {client_instance.instance_id}"
            )
        return result

    def _apply_implementer_files(
        self,
        payload: Dict[str, Any],
        turn_directory: Optional[str],
    ) -> None:
        """Apply file changes from Implementer output.

        Args:
            payload: The Implementer's result payload.
            turn_directory: Relative turn directory for file artifacts.
        """
        files = payload.get("files", [])
        if files:
            if turn_directory:
                self._logger.log(
                    f"    Applying {len(files)} files from Implementer..."
                )
                self._file_applier._apply_implementer_files(
                    payload,
                    turn_directory,
                )
            else:
                self._logger.log(
                    "    Skipping implementer files: missing turn directory."
                )

    def _has_clarification_requests(
        self,
        feedbacks: List[AgentFeedback],
    ) -> bool:
        """Check if any feedback needs clarification.

        Args:
            feedbacks: List of agent feedbacks.

        Returns:
            True if any feedback needs clarification.
        """
        return any(f.needs_clarification for f in feedbacks)

    def _handle_agent_clarifications(
        self,
        feedbacks: List[AgentFeedback],
    ) -> Dict[str, Answer]:
        """Handle clarification requests from agents.

        Args:
            feedbacks: List of agent feedbacks with clarification requests.

        Returns:
            Dict mapping question IDs to answers.
        """
        return self._feedback_loop.route_clarifications_to_user(feedbacks)

    def _merge_clarifications(
        self,
        context: Dict[str, Any],
        clarifications: Dict[str, Answer],
    ) -> Dict[str, Any]:
        """Merge clarification answers into context.

        Args:
            context: Current context.
            clarifications: Clarification answers.

        Returns:
            Updated context.
        """
        context["clarifications"].update(
            {q_id: ans.answer for q_id, ans in clarifications.items()}
        )
        return context

    def _merge_delegation_results(
        self,
        context: Dict[str, Any],
        results: List[ExecutionResult],
    ) -> Dict[str, Any]:
        """Merge delegation results into context.

        Args:
            context: Current context.
            results: List of execution results.

        Returns:
            Updated context.
        """
        for result in results:
            if result.success:
                context["completed_delegations"].append(result.delegation_id)
                context["agent_results"][result.delegation_id] = result.result

        context["iteration"] = context.get("iteration", 0) + 1
        return context

    def _persist_turn_artifacts(
        self,
        role_name: str,
        turn_id: int,
        prompt: str,
        turn_result: TurnResult,
    ) -> None:
        """Persist artifacts from a turn execution.

        Args:
            role_name: Name of the role.
            turn_id: Turn identifier.
            prompt: The prompt sent to the role.
            turn_result: The result from the turn.
        """
        turn_dir = self.runs_directory / role_name / f"turn_{turn_id}"
        self._ensure_directory(turn_dir)

        self._write_text(turn_dir / "prompt.txt", prompt)
        self._write_text(turn_dir / "assistant_text.txt", turn_result.assistant_text)

        if turn_result.full_items_text:
            self._write_text(turn_dir / "items_text.md", turn_result.full_items_text)

    def _persist_controller_state(self) -> None:
        """Persist the final orchestrator state."""
        state_path = self.runs_directory / "controller_state.json"
        state_json = json.dumps(self.state, indent=2, ensure_ascii=False)
        self._write_text(state_path, state_json)
        self._logger.log(f"State persisted to {state_path}")
