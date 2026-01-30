"""Lightweight logging helpers for the orchestrator."""
from __future__ import annotations

import time
from typing import Optional


class TimestampLogger:
    """Emit timestamped, flushed log messages."""

    def __init__(self, timestamp_format: Optional[str] = None) -> None:
        resolved_format = timestamp_format
        if resolved_format is None:
            import defaults

            resolved_format = defaults.DEFAULT_TIMESTAMP_FORMAT
        self._timestamp_format = resolved_format

    def _current_timestamp(self) -> str:
        """Return a formatted timestamp string."""
        timestamp_value = time.strftime(self._timestamp_format)
        return timestamp_value

    def log(self, message: str) -> None:
        """Print a timestamped message synchronously."""
        timestamp_value = self._current_timestamp()
        print(f"[{timestamp_value}] {message}", flush=True)
        return None
