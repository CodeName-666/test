"""Configuration type that drives the orchestrator runtime."""
from dataclasses import dataclass

from defualts.defaults import (
    DEFAULT_CYCLES,
    DEFAULT_PYTEST_CMD,
    DEFAULT_REPAIR_ATTEMPTS,
    DEFAULT_RUN_TESTS,
)


@dataclass
class OrchestratorConfig:
    """Configuration values that control orchestrator behavior."""

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
