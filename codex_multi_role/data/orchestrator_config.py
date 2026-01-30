"""Configuration type that drives the orchestrator runtime."""
from dataclasses import dataclass

from defaults import (
    DEFAULT_CYCLES,
    DEFAULT_PYTEST_CMD,
    DEFAULT_REPAIR_ATTEMPTS,
    DEFAULT_RUN_TESTS,
)
from ..validation_utils import ValidationMixin


@dataclass
class OrchestratorConfig(ValidationMixin):
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
        self._validate_non_empty_str(self.goal, "goal")
        self._validate_positive_int(self.cycles, "cycles")
        self._validate_non_negative_int(self.repair_attempts, "repair_attempts")
        self._validate_bool(self.run_tests, "run_tests")
        self._validate_non_empty_str(self.pytest_cmd, "pytest_cmd")
        return None
