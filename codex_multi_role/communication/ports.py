"""Ports (protocols) for communication module dependencies."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple


class LoggerPort(Protocol):
    """Logger protocol used by communication services."""

    def log(self, message: str) -> None:
        """Log one message."""
        ...


class RunStorePort(Protocol):
    """Run storage protocol used by communication services."""

    artifacts_directory: Path

    def load_answers(self) -> List[Dict[str, Any]]:
        """Load persisted answer records."""
        ...

    def load_pool(self) -> Dict[str, Any]:
        """Load current pool document."""
        ...

    def append_answer(
        self,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> bool:
        """Append one answer payload."""
        ...

    def append_inbox(
        self,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> bool:
        """Append one inbox payload."""
        ...

    def write_wave_documents(
        self,
        wave_index: int,
        compact_md: str,
        detailed_md: str,
    ) -> Tuple[Path, Path]:
        """Write compact and detailed wave documents."""
        ...

    def write_artifact(self, relative_path: str, content: str) -> Path:
        """Write one artifact file."""
        ...

    def merge_pool_entries(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge pool entries and return the updated pool."""
        ...


class ExecutionResultLike(Protocol):
    """Runtime protocol for delegation execution results."""

    delegation_id: str
    success: bool
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    duration_s: float


__all__ = ["ExecutionResultLike", "LoggerPort", "RunStorePort"]
