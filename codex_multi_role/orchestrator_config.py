"""Configuration type that drives the orchestrator runtime."""
from dataclasses import dataclass


@dataclass
class OrchestratorConfig:
    """Configuration values that control orchestrator behavior."""

    # High-level goal passed to every role.
    goal: str
    # Number of full role cycles to execute.
    cycles: int = 2
    # How many times to ask for JSON repair when parsing fails.
    repair_attempts: int = 1
    # Toggle pytest execution after implementer output is applied.
    run_tests: bool = False
    # Command line used to invoke pytest.
    pytest_cmd: str = "python -m pytest"
