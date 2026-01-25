"""Lightweight logging helpers for the orchestrator."""
from __future__ import annotations

import time


class TimestampLogger:
    """Emit timestamped, flushed log messages."""

    def __init__(self, timestamp_format: str = "%H:%M:%S") -> None:
        self._timestamp_format = timestamp_format

    def _current_timestamp(self) -> str:
        """Return a formatted timestamp string."""
        timestamp_value = time.strftime(self._timestamp_format)
        return timestamp_value

    def log(self, message: str) -> None:
        """Print a timestamped message synchronously."""
        timestamp_value = self._current_timestamp()
        print(f"[{timestamp_value}] {message}", flush=True)
        return None


DEFAULT_LOGGER = TimestampLogger()
