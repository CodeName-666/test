"""Delegation management for the Planner-as-Orchestrator architecture.

This module handles the lifecycle of agent delegations, including:
- Creating and validating delegation requests
- Resolving dependencies between delegations
- Grouping delegations for parallel execution
- Tracking delegation status
"""
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
    """Available agent types for delegation."""

    ARCHITECT = "architect"
    IMPLEMENTER = "implementer"
    INTEGRATOR = "integrator"


@dataclass
class Delegation:
    """Represents a single task delegation to an agent.

    Attributes:
        id: Unique identifier for this delegation.
        agent: Target agent type (architect, implementer, integrator).
        task: Description of the task to perform.
        priority: Priority level (1 = highest).
        depends_on: List of delegation IDs that must complete first.
        context: Additional context payload for the agent.
        parallel_group: Optional group name for parallel execution.
        status: Current status of the delegation.
        result: Result payload after completion.
        error: Error message if delegation failed.
    """

    id: str
    agent: str
    task: str
    priority: int = 1
    depends_on: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    parallel_group: Optional[str] = None
    status: DelegationStatus = DelegationStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate delegation fields after initialization."""
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Delegation id must be a non-empty string")
        if not self.task or not isinstance(self.task, str):
            raise ValueError("Delegation task must be a non-empty string")

        # Validate agent type
        valid_agents = {a.value for a in AgentType}
        if self.agent not in valid_agents:
            raise ValueError(
                f"Delegation agent must be one of {valid_agents}, got '{self.agent}'"
            )

        # Ensure depends_on is a list
        if not isinstance(self.depends_on, list):
            raise TypeError("depends_on must be a list")

        # Convert status string to enum if needed
        if isinstance(self.status, str):
            object.__setattr__(self, "status", DelegationStatus(self.status))


    def mark_running(self) -> None:
        """Mark this delegation as currently running."""
        self.status = DelegationStatus.RUNNING

    def mark_completed(self, result: Dict[str, Any]) -> None:
        """Mark this delegation as completed with a result.

        Args:
            result: The result payload from the agent.
        """
        self.status = DelegationStatus.COMPLETED
        self.result = result

    def mark_failed(self, error: str) -> None:
        """Mark this delegation as failed with an error message.

        Args:
            error: Description of what went wrong.
        """
        self.status = DelegationStatus.FAILED
        self.error = error

    def mark_needs_clarification(self) -> None:
        """Mark this delegation as needing clarification."""
        self.status = DelegationStatus.NEEDS_CLARIFICATION

    @property
    def is_complete(self) -> bool:
        """Check if delegation has finished (success or failure)."""
        return self.status in (
            DelegationStatus.COMPLETED,
            DelegationStatus.FAILED,
        )

    @property
    def is_ready(self) -> bool:
        """Check if delegation is ready to run (no pending dependencies)."""
        return self.status == DelegationStatus.PENDING


class DelegationManager:
    """Manages the lifecycle of agent delegations.

    Responsibilities:
    - Validate delegation requests from the Planner
    - Resolve dependencies between delegations
    - Group delegations for parallel execution
    - Track delegation status throughout execution
    """

    def __init__(
        self,
        available_agents: Optional[Set[str]] = None,
    ) -> None:
        """Initialize the delegation manager.

        Args:
            available_agents: Set of agent names that are available.
                Defaults to all AgentType values.
        """
        self._available_agents = available_agents or {a.value for a in AgentType}
        self._delegations: Dict[str, Delegation] = {}

    def create_delegations(
        self,
        delegation_specs: List[Dict[str, Any]],
    ) -> List[Delegation]:
        """Create validated Delegation objects from Planner output.

        Args:
            delegation_specs: List of delegation dictionaries from Planner JSON.

        Returns:
            List of validated Delegation objects.

        Raises:
            ValueError: If delegation specs are invalid.
        """
        delegations: List[Delegation] = []

        for spec in delegation_specs:
            delegation = Delegation(
                id=spec.get("id", ""),
                agent=spec.get("agent", ""),
                task=spec.get("task", ""),
                priority=spec.get("priority", 1),
                depends_on=spec.get("depends_on", []),
                context=spec.get("context", {}),
                parallel_group=spec.get("parallel_group"),
            )

            # Validate agent is available
            if delegation.agent not in self._available_agents:
                raise ValueError(
                    f"Agent '{delegation.agent}' is not available. "
                    f"Available agents: {self._available_agents}"
                )

            # Check for duplicate IDs
            if delegation.id in self._delegations:
                raise ValueError(f"Duplicate delegation ID: {delegation.id}")

            self._delegations[delegation.id] = delegation
            delegations.append(delegation)

        # Validate dependencies exist
        self._validate_dependencies(delegations)

        return delegations

    def _validate_dependencies(self, delegations: List[Delegation]) -> None:
        """Validate that all dependency references are valid.

        Args:
            delegations: List of delegations to validate.

        Raises:
            ValueError: If a dependency references an unknown delegation.
        """
        all_ids = {d.id for d in delegations} | set(self._delegations.keys())

        for delegation in delegations:
            for dep_id in delegation.depends_on:
                if dep_id not in all_ids:
                    raise ValueError(
                        f"Delegation '{delegation.id}' depends on unknown "
                        f"delegation '{dep_id}'"
                    )

    def get_execution_order(
        self,
        delegations: List[Delegation],
    ) -> List[List[Delegation]]:
        """Return delegations grouped by execution wave.

        Each wave contains delegations that can run in parallel.
        Dependencies are resolved across waves using topological sort.

        Args:
            delegations: List of delegations to order.

        Returns:
            List of waves, where each wave is a list of delegations
            that can execute in parallel.

        Raises:
            ValueError: If circular dependencies are detected.
        """
        if not delegations:
            return []

        # Build dependency graph
        delegation_map = {d.id: d for d in delegations}
        dependency_graph: Dict[str, Set[str]] = {
            d.id: set(d.depends_on) for d in delegations
        }

        waves: List[List[Delegation]] = []
        remaining = set(delegation_map.keys())
        completed: Set[str] = set()

        # Add already completed delegations from previous runs
        for d_id, d in self._delegations.items():
            if d.status == DelegationStatus.COMPLETED:
                completed.add(d_id)

        while remaining:
            # Find delegations with all dependencies satisfied
            ready = {
                d_id
                for d_id in remaining
                if dependency_graph[d_id].issubset(completed)
            }

            if not ready:
                # Check for circular dependencies
                raise ValueError(
                    f"Circular dependency detected. Remaining delegations: "
                    f"{remaining}, completed: {completed}"
                )

            # Sort by priority within the wave (lower number = higher priority)
            wave = sorted(
                [delegation_map[d_id] for d_id in ready],
                key=lambda d: d.priority,
            )
            waves.append(wave)

            completed.update(ready)
            remaining -= ready

        return waves

    def get_parallel_groups(
        self,
        delegations: List[Delegation],
    ) -> Dict[Optional[str], List[Delegation]]:
        """Group delegations by their parallel_group.

        Args:
            delegations: List of delegations to group.

        Returns:
            Dict mapping parallel_group names to lists of delegations.
            Delegations with no parallel_group are under None key.
        """
        groups: Dict[Optional[str], List[Delegation]] = {}

        for delegation in delegations:
            group = delegation.parallel_group
            if group not in groups:
                groups[group] = []
            groups[group].append(delegation)

        return groups

    def get_delegation(self, delegation_id: str) -> Optional[Delegation]:
        """Get a delegation by its ID.

        Args:
            delegation_id: The ID of the delegation to retrieve.

        Returns:
            The Delegation object, or None if not found.
        """
        return self._delegations.get(delegation_id)

    def update_delegation_status(
        self,
        delegation_id: str,
        status: DelegationStatus,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update the status of a delegation.

        Args:
            delegation_id: ID of the delegation to update.
            status: New status to set.
            result: Optional result payload (for completed status).
            error: Optional error message (for failed status).

        Raises:
            KeyError: If delegation_id is not found.
        """
        if delegation_id not in self._delegations:
            raise KeyError(f"Unknown delegation ID: {delegation_id}")

        delegation = self._delegations[delegation_id]
        delegation.status = status

        if result is not None:
            delegation.result = result
        if error is not None:
            delegation.error = error

    def get_pending_delegations(self) -> List[Delegation]:
        """Get all delegations that are still pending.

        Returns:
            List of delegations with PENDING status.
        """
        return [
            d
            for d in self._delegations.values()
            if d.status == DelegationStatus.PENDING
        ]

    def get_completed_delegations(self) -> List[Delegation]:
        """Get all delegations that have completed successfully.

        Returns:
            List of delegations with COMPLETED status.
        """
        return [
            d
            for d in self._delegations.values()
            if d.status == DelegationStatus.COMPLETED
        ]

    def get_failed_delegations(self) -> List[Delegation]:
        """Get all delegations that have failed.

        Returns:
            List of delegations with FAILED status.
        """
        return [
            d
            for d in self._delegations.values()
            if d.status == DelegationStatus.FAILED
        ]

    def clear(self) -> None:
        """Clear all tracked delegations."""
        self._delegations.clear()

    def can_skip_architect(
        self,
        task: str,
        context: Dict[str, Any],
    ) -> bool:
        """Determine if Architect can be skipped for a trivial task.

        This is a heuristic to allow simple tasks to go directly
        to the Implementer without architectural analysis.

        Args:
            task: The task description.
            context: Additional context about the task.

        Returns:
            True if Architect can be skipped, False otherwise.
        """
        # Heuristics for trivial tasks
        trivial_indicators = [
            "bug fix",
            "typo",
            "simple",
            "minor",
            "small change",
            "rename",
            "update comment",
            "add log",
            "einfach",  # German: simple
            "klein",  # German: small
            "bugfix",
            "hotfix",
        ]

        task_lower = task.lower()
        return any(indicator in task_lower for indicator in trivial_indicators)
