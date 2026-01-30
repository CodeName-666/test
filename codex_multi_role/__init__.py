"""Package for the codex multi-role orchestrator."""

from __future__ import annotations

from typing import Any

__all__ = ["CodexRunsOrchestratorV2"]


def __getattr__(name: str) -> Any:
    """Provide lazy attribute access to avoid import-time cycles."""
    result: Any = None
    if name == "CodexRunsOrchestratorV2":
        from .orchestrator import CodexRunsOrchestratorV2

        result = CodexRunsOrchestratorV2
    else:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
    return result
