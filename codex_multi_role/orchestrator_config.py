"""Configuration type that drives the orchestrator runtime."""
from dataclasses import dataclass


@dataclass
class OrchestratorConfig:
    goal: str
    cycles: int = 2
    repair_attempts: int = 1
    run_tests: bool = False
    pytest_cmd: str = "python -m pytest"
