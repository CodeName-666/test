"""Dynamic role client factory for multi-instance support.

This module provides a factory for creating and managing role clients dynamically,
enabling multiple instances of the same role to run in parallel.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from ..roles.role_client import RoleClient
from ..client.codex_role_client import CodexRoleClient
from ..roles.role_spec import RoleSpec


@dataclass
class ClientInstance:
    """Represents a single client instance.

    Attributes:
        instance_id: Unique identifier for this instance.
        role_name: Name of the role this client serves.
        client: The actual RoleClient instance.
        in_use: Whether this instance is currently executing a task.
        delegation_id: ID of the delegation currently using this instance.
    """

    instance_id: str
    role_name: str
    client: RoleClient
    in_use: bool = False
    delegation_id: Optional[str] = None

    def acquire(self, delegation_id: str) -> None:
        """Mark this instance as in use."""
        self.in_use = True
        self.delegation_id = delegation_id

    def release(self) -> None:
        """Release this instance back to the pool."""
        self.in_use = False
        self.delegation_id = None


class RoleClientFactory:
    """Factory for dynamically creating and managing role clients.

    Supports creating multiple instances of the same role for parallel execution.
    Clients are created on demand and can be released after use.
    """

    def __init__(
        self,
        role_specs: Dict[str, RoleSpec],
        runs_directory: Path,
        ensure_directory: Callable[[Path], None],
        max_instances_per_role: int = 4,
    ) -> None:
        """Initialize the factory.

        Args:
            role_specs: Dictionary of role specifications by name.
            runs_directory: Base directory for run artifacts.
            ensure_directory: Callable to create directories.
            max_instances_per_role: Maximum concurrent instances per role.
        """
        self._role_specs = role_specs
        self._runs_directory = runs_directory
        self._ensure_directory = ensure_directory
        self._max_instances = max_instances_per_role

        # Track all instances by role
        self._instances: Dict[str, List[ClientInstance]] = {
            name: [] for name in role_specs.keys()
        }

        # Thread safety
        self._lock = threading.Lock()

        # Track active instances for cleanup
        self._active_count: Dict[str, int] = {name: 0 for name in role_specs.keys()}

    def acquire_client(self, role_name: str, delegation_id: str) -> ClientInstance:
        """Acquire a client instance for a role.

        Creates a new instance if needed, or reuses an idle one.

        Args:
            role_name: Name of the role to get a client for.
            delegation_id: ID of the delegation that will use this client.

        Returns:
            ClientInstance ready for use.

        Raises:
            ValueError: If role_name is not recognized.
            RuntimeError: If max instances reached and none available.
        """
        if role_name not in self._role_specs:
            raise ValueError(f"Unknown role: {role_name}")

        with self._lock:
            # Try to find an idle instance
            for instance in self._instances[role_name]:
                if not instance.in_use:
                    instance.acquire(delegation_id)
                    return instance

            # Check if we can create a new instance
            if self._active_count[role_name] >= self._max_instances:
                raise RuntimeError(
                    f"Maximum instances ({self._max_instances}) reached for role '{role_name}'. "
                    f"No idle instances available."
                )

            # Create new instance
            instance = self._create_instance(role_name, delegation_id)
            self._instances[role_name].append(instance)
            self._active_count[role_name] += 1

            return instance

    def release_client(self, instance: ClientInstance, stop: bool = False) -> None:
        """Release a client instance back to the pool.

        Args:
            instance: The instance to release.
            stop: If True, stop the client and remove from pool.
        """
        with self._lock:
            instance.release()

            if stop:
                self._stop_and_remove(instance)

    def _create_instance(self, role_name: str, delegation_id: str) -> ClientInstance:
        """Create a new client instance for a role.

        Args:
            role_name: Name of the role.
            delegation_id: ID of the delegation that will use this client.

        Returns:
            New ClientInstance.
        """
        spec = self._role_specs[role_name]
        instance_id = f"{role_name}_{uuid.uuid4().hex[:8]}"

        client = CodexRoleClient(
            role_name=spec.name,
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
        )

        # Setup events file with instance-specific path
        events_dir = self._runs_directory / role_name / "instances" / instance_id
        self._ensure_directory(events_dir)
        client.events_file = events_dir / "events.jsonl"

        instance = ClientInstance(
            instance_id=instance_id,
            role_name=role_name,
            client=client,
        )
        instance.acquire(delegation_id)

        return instance

    def _stop_and_remove(self, instance: ClientInstance) -> None:
        """Stop a client and remove from pool.

        Args:
            instance: The instance to stop and remove.
        """
        try:
            instance.client.stop()
        except Exception:
            pass  # Best effort cleanup

        role_name = instance.role_name
        if instance in self._instances[role_name]:
            self._instances[role_name].remove(instance)
            self._active_count[role_name] -= 1

    def get_active_count(self, role_name: str) -> int:
        """Get the number of active instances for a role.

        Args:
            role_name: Name of the role.

        Returns:
            Number of active instances.
        """
        with self._lock:
            return self._active_count.get(role_name, 0)

    def get_in_use_count(self, role_name: str) -> int:
        """Get the number of instances currently in use for a role.

        Args:
            role_name: Name of the role.

        Returns:
            Number of instances currently executing tasks.
        """
        with self._lock:
            return sum(1 for inst in self._instances.get(role_name, []) if inst.in_use)

    def stop_all(self) -> None:
        """Stop all client instances."""
        with self._lock:
            for role_name, instances in self._instances.items():
                for instance in instances:
                    try:
                        instance.client.stop()
                    except Exception:
                        pass  # Best effort cleanup
                instances.clear()
                self._active_count[role_name] = 0

    def get_stats(self) -> Dict[str, Dict[str, int]]:
        """Get statistics about client instances.

        Returns:
            Dict with stats per role: total, in_use, idle.
        """
        with self._lock:
            stats = {}
            for role_name in self._role_specs.keys():
                instances = self._instances[role_name]
                in_use = sum(1 for i in instances if i.in_use)
                stats[role_name] = {
                    "total": len(instances),
                    "in_use": in_use,
                    "idle": len(instances) - in_use,
                    "max": self._max_instances,
                }
            return stats
