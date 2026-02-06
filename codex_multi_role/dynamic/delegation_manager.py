"""Delegation lifecycle management for planner-gated orchestration."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class DelegationStatus(Enum):
    """Status of a delegation in its lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_CLARIFICATION = "needs_clarification"


class AgentType(Enum):
    """Default built-in agent identifiers.

    The manager also supports arbitrary agent names provided at runtime
    via `available_agents`.
    """

    ARCHITECT = "architect"
    IMPLEMENTER = "implementer"
    INTEGRATOR = "integrator"


@dataclass
class Delegation:
    """Single delegation payload from planner to a worker agent.

    Attributes:
        delegation_id: Stable delegation identifier.
        agent_id: Target agent identifier.
        task_description: Delegated task description.
        acceptance_criteria: Criteria that define task completion.
        required_inputs: Required input identifiers.
        provided_inputs: Input identifiers currently available to the worker.
        depends_on: Delegations that must complete before this one can start.
        context: Additional planner-provided context payload.
        priority: Optional execution priority (lower value = higher priority).
        parallel_group: Optional parallel group identifier.
        turn_directory: Turn artifact directory when execution has started.
        status: Current lifecycle status.
        result: Structured result payload after completion.
        error: Error details for failed delegations.
    """

    delegation_id: str
    agent_id: str
    task_description: str
    acceptance_criteria: List[str] = field(default_factory=list)
    required_inputs: List[str] = field(default_factory=list)
    provided_inputs: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    priority: int = 1
    parallel_group: Optional[str] = None
    turn_directory: Optional[str] = None
    status: DelegationStatus = DelegationStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate delegation fields after initialization.

        Raises:
            TypeError: If field types are invalid.
            ValueError: If required fields are empty or invalid.
        """
        self._validate_non_empty_string(self.delegation_id, "delegation_id")
        self._validate_non_empty_string(self.agent_id, "agent_id")
        self._validate_non_empty_string(self.task_description, "task_description")
        self._validate_string_list(self.acceptance_criteria, "acceptance_criteria")
        self._validate_string_list(self.required_inputs, "required_inputs")
        self._validate_string_list(self.provided_inputs, "provided_inputs")
        self._validate_string_list(self.depends_on, "depends_on")
        if not isinstance(self.context, dict):
            raise TypeError("context must be a dictionary")
        if not isinstance(self.priority, int):
            raise TypeError("priority must be an integer")
        if self.priority < 1:
            raise ValueError("priority must be >= 1")
        if self.parallel_group is not None and not isinstance(self.parallel_group, str):
            raise TypeError("parallel_group must be a string or None")
        if isinstance(self.status, str):
            object.__setattr__(self, "status", DelegationStatus(self.status))
        elif not isinstance(self.status, DelegationStatus):
            raise TypeError("status must be a DelegationStatus or string value")

    @property
    def id(self) -> str:
        """Backward-compatible alias for `delegation_id`."""
        result = self.delegation_id
        return result

    @property
    def agent(self) -> str:
        """Backward-compatible alias for `agent_id`."""
        result = self.agent_id
        return result

    @property
    def task(self) -> str:
        """Backward-compatible alias for `task_description`."""
        result = self.task_description
        return result

    @property
    def missing_required_inputs(self) -> List[str]:
        """Get missing required input identifiers."""
        missing = sorted(set(self.required_inputs) - set(self.provided_inputs))
        return missing

    @property
    def has_complete_inputs(self) -> bool:
        """Check whether all required inputs are provided."""
        result = not self.missing_required_inputs
        return result

    @property
    def is_complete(self) -> bool:
        """Check if delegation has finished (success, failure, or blocked)."""
        result = self.status in (
            DelegationStatus.COMPLETED,
            DelegationStatus.FAILED,
            DelegationStatus.BLOCKED,
        )
        return result

    @property
    def is_ready(self) -> bool:
        """Check if delegation is ready to run."""
        result = self.status == DelegationStatus.PENDING
        return result

    def mark_running(self) -> None:
        """Mark this delegation as running."""
        self.status = DelegationStatus.RUNNING

    def mark_completed(self, result: Dict[str, Any]) -> None:
        """Mark this delegation as completed.

        Args:
            result: Result payload returned by the worker.
        """
        self.status = DelegationStatus.COMPLETED
        self.result = result
        self.error = None

    def mark_failed(self, error: str) -> None:
        """Mark this delegation as failed.

        Args:
            error: Failure reason.
        """
        self.status = DelegationStatus.FAILED
        self.error = error

    def mark_blocked(self, error: str) -> None:
        """Mark this delegation as blocked.

        Args:
            error: Blocking reason.
        """
        self.status = DelegationStatus.BLOCKED
        self.error = error

    def mark_needs_clarification(self) -> None:
        """Mark this delegation as needing clarification."""
        self.status = DelegationStatus.NEEDS_CLARIFICATION

    def _validate_non_empty_string(self, value: Any, field_name: str) -> None:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string")
        if not value.strip():
            raise ValueError(f"{field_name} must not be empty")

    def _validate_string_list(self, value: Any, field_name: str) -> None:
        if not isinstance(value, list):
            raise TypeError(f"{field_name} must be a list")
        for index, item in enumerate(value):
            if not isinstance(item, str):
                raise TypeError(f"{field_name}[{index}] must be a string")


class DelegationManager:
    """Create, validate, and order planner delegations."""

    def __init__(
        self,
        available_agents: Optional[Set[str]] = None,
    ) -> None:
        """Initialize delegation manager.

        Args:
            available_agents: Agents that may be delegated to.
        """
        default_agents = {agent_type.value for agent_type in AgentType}
        self._available_agents = available_agents or default_agents
        self._delegations: Dict[str, Delegation] = {}

    def create_delegations(
        self,
        delegation_specs: List[Dict[str, Any]],
    ) -> List[Delegation]:
        """Create validated Delegation objects from planner payload.

        Args:
            delegation_specs: Planner delegation specifications.

        Returns:
            Validated delegation list.

        Raises:
            TypeError: If input structure is invalid.
            ValueError: If required fields, dependencies, or input gates fail.
        """
        if not isinstance(delegation_specs, list):
            raise TypeError("delegation_specs must be a list")

        delegations: List[Delegation] = []
        for index, spec in enumerate(delegation_specs):
            if isinstance(spec, dict):
                delegation = self._create_single_delegation(spec, index)
                self._validate_agent_available(delegation)
                self._validate_unique_delegation_id(delegation)
                self._delegations[delegation.delegation_id] = delegation
                delegations.append(delegation)
            else:
                raise TypeError(f"delegation_specs[{index}] must be a dictionary")

        self._validate_dependencies(delegations)
        self._validate_input_completeness_gate(delegations)
        return delegations

    def _create_single_delegation(
        self,
        spec: Dict[str, Any],
        index: int,
    ) -> Delegation:
        delegation_id = self._read_required_string(spec, ["delegation_id", "id"], "")
        agent_id = self._read_required_string(spec, ["agent_id", "agent"], "")
        task_description = self._read_required_string(
            spec, ["task_description", "task"], ""
        )
        acceptance_criteria, required_inputs, provided_inputs, depends_on = (
            self._read_delegation_list_fields(spec, index)
        )
        context = spec.get("context", {})
        if context is None:
            context = {}
        if not isinstance(context, dict):
            raise TypeError(f"delegation_specs[{index}].context must be a dictionary")
        parallel_group = spec.get("parallel_group")
        if parallel_group is not None and not isinstance(parallel_group, str):
            raise TypeError(
                f"delegation_specs[{index}].parallel_group must be a string or None"
            )
        priority_value = spec.get("priority", 1)
        if not isinstance(priority_value, int):
            raise TypeError(f"delegation_specs[{index}].priority must be an integer")
        if priority_value < 1:
            raise ValueError(f"delegation_specs[{index}].priority must be >= 1")
        delegation = Delegation(
            delegation_id=delegation_id,
            agent_id=agent_id,
            task_description=task_description,
            acceptance_criteria=acceptance_criteria,
            required_inputs=required_inputs,
            provided_inputs=provided_inputs,
            depends_on=depends_on,
            context=context,
            priority=priority_value,
            parallel_group=parallel_group,
        )
        return delegation

    def _read_delegation_list_fields(
        self,
        spec: Dict[str, Any],
        index: int,
    ) -> tuple[List[str], List[str], List[str], List[str]]:
        acceptance_criteria = self._read_string_list(
            spec,
            keys=["acceptance_criteria"],
            default=[],
            field_name=f"delegation_specs[{index}].acceptance_criteria",
        )
        required_inputs = self._read_string_list(
            spec,
            keys=["required_inputs"],
            default=[],
            field_name=f"delegation_specs[{index}].required_inputs",
        )
        provided_inputs = self._read_string_list(
            spec,
            keys=["provided_inputs"],
            default=[],
            field_name=f"delegation_specs[{index}].provided_inputs",
        )
        depends_on = self._read_string_list(
            spec,
            keys=["depends_on"],
            default=[],
            field_name=f"delegation_specs[{index}].depends_on",
        )
        return acceptance_criteria, required_inputs, provided_inputs, depends_on

    def _read_required_string(
        self,
        spec: Dict[str, Any],
        keys: List[str],
        default: str,
    ) -> str:
        value: Any = default
        for key in keys:
            if key in spec:
                value = spec[key]
                break
        if not isinstance(value, str):
            joined_keys = ", ".join(keys)
            raise TypeError(f"delegation field '{joined_keys}' must be a string")
        result = value.strip()
        if not result:
            joined_keys = ", ".join(keys)
            raise ValueError(f"delegation field '{joined_keys}' must not be empty")
        return result

    def _read_string_list(
        self,
        spec: Dict[str, Any],
        keys: List[str],
        default: List[str],
        field_name: str,
    ) -> List[str]:
        raw_value: Any = default
        for key in keys:
            if key in spec:
                raw_value = spec[key]
                break
        if not isinstance(raw_value, list):
            raise TypeError(f"{field_name} must be a list")
        normalized: List[str] = []
        for index, item in enumerate(raw_value):
            if isinstance(item, str):
                normalized.append(item)
            else:
                raise TypeError(f"{field_name}[{index}] must be a string")
        return normalized

    def _validate_agent_available(self, delegation: Delegation) -> None:
        if delegation.agent_id not in self._available_agents:
            available = sorted(self._available_agents)
            raise ValueError(
                f"agent '{delegation.agent_id}' is not available. "
                f"Available agents: {available}"
            )

    def _validate_unique_delegation_id(self, delegation: Delegation) -> None:
        if delegation.delegation_id in self._delegations:
            raise ValueError(f"duplicate delegation_id: {delegation.delegation_id}")

    def _validate_dependencies(self, delegations: List[Delegation]) -> None:
        all_ids = {delegation.delegation_id for delegation in delegations}
        all_ids.update(self._delegations.keys())
        for delegation in delegations:
            for dependency_id in delegation.depends_on:
                if dependency_id not in all_ids:
                    raise ValueError(
                        f"delegation '{delegation.delegation_id}' depends on unknown "
                        f"delegation '{dependency_id}'"
                    )

    def _validate_input_completeness_gate(self, delegations: List[Delegation]) -> None:
        blocked_messages: List[str] = []
        for delegation in delegations:
            missing_inputs = delegation.missing_required_inputs
            if missing_inputs:
                message = (
                    f"delegation '{delegation.delegation_id}' missing required inputs: "
                    f"{missing_inputs}"
                )
                delegation.mark_blocked(message)
                blocked_messages.append(message)
        if blocked_messages:
            details = "; ".join(blocked_messages)
            raise ValueError(f"required/provided input gate failed: {details}")

    def get_execution_order(
        self,
        delegations: List[Delegation],
    ) -> List[List[Delegation]]:
        """Group executable delegations into dependency-respecting waves."""
        waves: List[List[Delegation]]
        executable = [
            delegation
            for delegation in delegations
            if delegation.status != DelegationStatus.BLOCKED
        ]
        if executable:
            delegation_map = {
                delegation.delegation_id: delegation for delegation in executable
            }
            dependency_graph: Dict[str, Set[str]] = {
                delegation.delegation_id: set(delegation.depends_on)
                for delegation in executable
            }
            completed: Set[str] = set()
            for delegation_id, delegation in self._delegations.items():
                if delegation.status == DelegationStatus.COMPLETED:
                    completed.add(delegation_id)
            remaining = set(delegation_map.keys())
            waves = []
            while remaining:
                ready = {
                    delegation_id
                    for delegation_id in remaining
                    if dependency_graph[delegation_id].issubset(completed)
                }
                if not ready:
                    raise ValueError(
                        "circular or unsatisfied dependency detected for delegations: "
                        f"{sorted(remaining)}"
                    )
                wave = sorted(
                    [delegation_map[delegation_id] for delegation_id in ready],
                    key=lambda delegation: delegation.priority,
                )
                waves.append(wave)
                completed.update(ready)
                remaining -= ready
        else:
            waves = []
        return waves

    def get_parallel_groups(
        self,
        delegations: List[Delegation],
    ) -> Dict[Optional[str], List[Delegation]]:
        """Group delegations by `parallel_group`."""
        groups: Dict[Optional[str], List[Delegation]] = {}
        for delegation in delegations:
            group = delegation.parallel_group
            existing = groups.get(group)
            if existing is None:
                groups[group] = [delegation]
            else:
                existing.append(delegation)
        return groups

    def get_delegation(self, delegation_id: str) -> Optional[Delegation]:
        """Get delegation by id."""
        result = self._delegations.get(delegation_id)
        return result

    def update_delegation_status(
        self,
        delegation_id: str,
        status: DelegationStatus,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update delegation status and optional result/error payloads."""
        delegation = self._delegations.get(delegation_id)
        if delegation is None:
            raise KeyError(f"unknown delegation_id: {delegation_id}")
        delegation.status = status
        if result is not None:
            delegation.result = result
        if error is not None:
            delegation.error = error

    def get_pending_delegations(self) -> List[Delegation]:
        """Return delegations still pending."""
        result = [
            delegation
            for delegation in self._delegations.values()
            if delegation.status == DelegationStatus.PENDING
        ]
        return result

    def get_completed_delegations(self) -> List[Delegation]:
        """Return successfully completed delegations."""
        result = [
            delegation
            for delegation in self._delegations.values()
            if delegation.status == DelegationStatus.COMPLETED
        ]
        return result

    def get_failed_delegations(self) -> List[Delegation]:
        """Return failed delegations."""
        result = [
            delegation
            for delegation in self._delegations.values()
            if delegation.status == DelegationStatus.FAILED
        ]
        return result

    def get_blocked_delegations(self) -> List[Delegation]:
        """Return blocked delegations."""
        result = [
            delegation
            for delegation in self._delegations.values()
            if delegation.status == DelegationStatus.BLOCKED
        ]
        return result

    def clear(self) -> None:
        """Remove all tracked delegations."""
        self._delegations.clear()

    def can_skip_architect(
        self,
        task: str,
        context: Dict[str, Any],
    ) -> bool:
        """Heuristic for routing trivial tasks directly to implementer.

        Args:
            task: Task description.
            context: Additional context. Present for future extension.

        Returns:
            True if the task likely does not require architectural analysis.
        """
        if not isinstance(task, str):
            raise TypeError("task must be a string")
        if not isinstance(context, dict):
            raise TypeError("context must be a dictionary")
        trivial_indicators = [
            "bug fix",
            "typo",
            "simple",
            "minor",
            "small change",
            "rename",
            "update comment",
            "add log",
            "einfach",
            "klein",
            "bugfix",
            "hotfix",
        ]
        task_lower = task.lower()
        result = any(indicator in task_lower for indicator in trivial_indicators)
        return result
