"""Configuration type that drives the orchestrator runtime."""
from dataclasses import dataclass

from defaults import (
    DEFAULT_CYCLES,
    DEFAULT_PYTEST_CMD,
    DEFAULT_REPAIR_ATTEMPTS,
    DEFAULT_RUN_TESTS,
)


@dataclass
class OrchestratorConfig:
    """Configuration values that control orchestrator behavior.

    Attributes:
        goal: Goal text passed to each role.
        cycles: Number of full role cycles to execute.
        repair_attempts: Number of JSON repair retries.
        run_tests: Toggle to run pytest after implementer output is applied.
        pytest_cmd: Command line used to invoke pytest.

    Raises:
        TypeError: If any field has an invalid type.
        ValueError: If numeric fields are out of allowed ranges or goal is empty.
    """

    # High-level goal passed to every role.
    goal: str
    # Number of full role cycles to execute.
    cycles: int = DEFAULT_CYCLES
    # How many times to ask for JSON repair when parsing fails.
    repair_attempts: int = DEFAULT_REPAIR_ATTEMPTS
    # Toggle pytest execution after implementer output is applied.
    run_tests: bool = DEFAULT_RUN_TESTS
    # Command line used to invoke pytest.
    pytest_cmd: str = DEFAULT_PYTEST_CMD

    def __post_init__(self) -> None:
        """Validate configuration values after initialization.

        Raises:
            TypeError: If any field has an invalid type.
            ValueError: If numeric fields are out of allowed ranges or goal is empty.
        """
        self._validate_goal()
        self._validate_cycles()
        self._validate_repair_attempts()
        self._validate_run_tests()
        self._validate_pytest_cmd()
        return None

    def _validate_goal(self) -> None:
        """Validate the goal field."""
        if isinstance(self.goal, str):
            if self.goal.strip():
                pass
            else:
                raise ValueError("goal must not be empty")
        else:
            raise TypeError("goal must be a string")
        return None

    def _validate_cycles(self) -> None:
        """Validate the cycles field."""
        if isinstance(self.cycles, int):
            if self.cycles > 0:
                pass
            else:
                raise ValueError("cycles must be greater than zero")
        else:
            raise TypeError("cycles must be an integer")
        return None

    def _validate_repair_attempts(self) -> None:
        """Validate the repair_attempts field."""
        if isinstance(self.repair_attempts, int):
            if self.repair_attempts >= 0:
                pass
            else:
                raise ValueError("repair_attempts must be zero or greater")
        else:
            raise TypeError("repair_attempts must be an integer")
        return None

    def _validate_run_tests(self) -> None:
        """Validate the run_tests field."""
        if isinstance(self.run_tests, bool):
            pass
        else:
            raise TypeError("run_tests must be a boolean")
        return None

    def _validate_pytest_cmd(self) -> None:
        """Validate the pytest_cmd field."""
        if isinstance(self.pytest_cmd, str):
            if self.pytest_cmd.strip():
                pass
            else:
                raise ValueError("pytest_cmd must not be empty")
        else:
            raise TypeError("pytest_cmd must be a string")
        return None
