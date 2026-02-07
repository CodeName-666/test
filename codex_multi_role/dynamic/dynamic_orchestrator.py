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
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..communication import (
    AgentFeedback,
    CommunicationCoordinator,
    CommunicationEngine,
    ConsoleUserInteraction,
    FeedbackLoop,
    FeedbackStatus,
    PlannerDecision,
    UserInteraction,
)
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
from .parallel_executor import ExecutionResult, ParallelExecutor
from .role_client_factory import RoleClientFactory
from .run_store import RunStore


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
        self._initialize_execution_counters()
        self._initialize_run_components(
            max_parallel_workers=max_parallel_workers,
            goal=configuration.goal,
        )
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
        self._communication_coordinator = CommunicationCoordinator(
            run_store=self._run_store,
            runs_directory=self.runs_directory,
            planner_compact_artifact_relative=self._planner_compact_artifact_relative,
            feedback_loop=self._feedback_loop,
            user_interaction=self._user_interaction,
            logger=self._logger,
            redact_payload=redact_secrets,
            record_manifest_event=self._record_manifest_event,
            build_idempotency_key=self._build_idempotency_key,
            record_metric=self._record_metric,
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
        max_iterations = self.configuration.cycles * 10
        communication_engine = CommunicationEngine(
            run_id=self.run_id,
            goal=self.configuration.goal,
            max_iterations=max_iterations,
            initial_wave_index=self._wave_counter,
            logger=self._logger,
            coordinator=self._communication_coordinator,
            start_clients=self.start_all,
            stop_clients=self.stop_all,
            run_planner=self._run_planner,
            execute_delegations=self._execute_delegations,
            update_delegation_status=self._update_delegation_status_from_feedback,
            record_manifest_event=self._record_manifest_event,
            build_idempotency_key=self._build_idempotency_key,
            persist_controller_state=self._persist_controller_state,
        )
        self._context = communication_engine.run()
        self._wave_counter = communication_engine.wave_counter

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
            context_packet = self._communication_coordinator.build_context_packet_for_delegation()
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
            self._communication_coordinator.persist_worker_payload(
                delegation_id=delegation.delegation_id,
                agent_id=delegation.agent_id,
                payload=payload,
            )
            result_payload = payload
        finally:
            self._client_factory.release_client(client_instance)
            self._logger.log(
                f"    Released client instance {client_instance.instance_id}"
            )
        return result_payload

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
