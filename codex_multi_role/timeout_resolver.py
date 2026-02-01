"""Timeout resolution helpers for the Codex orchestrator."""
from __future__ import annotations

from typing import Tuple

from defaults import (
    DEFAULT_PLANNER_TIMEOUT_S,
    DEFAULT_ROLE_TIMEOUT_S,
    PLANNER_TIMEOUT_ENV,
    ROLE_TIMEOUT_ENV,
)
from .roles.data.role_spec_models import RoleSpec
from .utils.env_utils import EnvironmentReader


class TimeoutResolver:
    """Resolve timeout values for role execution."""

    def __init__(self, environment_reader: EnvironmentReader) -> None:
        """Initialize the timeout resolver.

        Args:
            environment_reader: Reader for environment/config values.

        Raises:
            TypeError: If environment_reader is not an EnvironmentReader.
        """
        if isinstance(environment_reader, EnvironmentReader):
            self._environment_reader = environment_reader
        else:
            raise TypeError("environment_reader must be an EnvironmentReader")

    def _resolve_timeouts(self) -> Tuple[float, float]:
        """Resolve planner and role timeouts from environment/config.

        Returns:
            Tuple of (planner_timeout, role_timeout) in seconds.
        """
        planner_timeout = self._environment_reader.get_float(
            PLANNER_TIMEOUT_ENV,
            DEFAULT_PLANNER_TIMEOUT_S,
        )
        role_timeout = self._environment_reader.get_float(
            ROLE_TIMEOUT_ENV,
            DEFAULT_ROLE_TIMEOUT_S,
        )
        result = (planner_timeout, role_timeout)
        return result

    def _select_timeout(
        self,
        role_spec: RoleSpec,
        planner_timeout: float,
        role_timeout: float,
    ) -> float:
        """Select timeout based on role behavior configuration.

        Args:
            role_spec: Role specification for the current role.
            planner_timeout: Planner timeout in seconds.
            role_timeout: Default role timeout in seconds.

        Returns:
            Timeout value to use for the role.

        Raises:
            TypeError: If inputs have invalid types.
            ValueError: If timeout values are not positive.
        """
        if not isinstance(role_spec, RoleSpec):
            raise TypeError("role_spec must be a RoleSpec")
        if isinstance(planner_timeout, (int, float)):
            if planner_timeout > 0:
                planner_value = float(planner_timeout)
            else:
                raise ValueError("planner_timeout must be greater than zero")
        else:
            raise TypeError("planner_timeout must be a number")
        if isinstance(role_timeout, (int, float)):
            if role_timeout > 0:
                role_value = float(role_timeout)
            else:
                raise ValueError("role_timeout must be greater than zero")
        else:
            raise TypeError("role_timeout must be a number")

        timeout_value = role_value
        if role_spec.behaviors.timeout_policy == "planner":
            timeout_value = planner_value
        result = timeout_value
        return result
