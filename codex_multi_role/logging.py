"""Lightweight logging helpers for the orchestrator."""
from __future__ import annotations

import time
from typing import Optional


class TimestampLogger:
    """Emit timestamped, flushed log messages."""

    def __init__(self, timestamp_format: Optional[str] = None) -> None:
        """Initialize the logger with an optional timestamp format.

        Args:
            timestamp_format: Optional strftime-compatible format string. When None,
                the default format from defaults.DEFAULT_TIMESTAMP_FORMAT is used.

        Raises:
            TypeError: If timestamp_format is not a string or None.
            ValueError: If timestamp_format is an empty string.
        """
        resolved_format = ""
        if timestamp_format is None:
            import defaults

            resolved_format = defaults.DEFAULT_TIMESTAMP_FORMAT
        elif isinstance(timestamp_format, str):
            if timestamp_format.strip():
                resolved_format = timestamp_format
            else:
                raise ValueError("timestamp_format must not be empty")
        else:
            raise TypeError("timestamp_format must be a string or None")
        self._timestamp_format = resolved_format
        return None

    def _current_timestamp(self) -> str:
        """Return a formatted timestamp string.

        Returns:
            Formatted timestamp string based on the configured format.
        """
        timestamp_value = time.strftime(self._timestamp_format)
        return timestamp_value

    def log(self, message: str) -> None:
        """Print a timestamped message synchronously.

        Args:
            message: Message string to log.

        Raises:
            TypeError: If message is not a string.
        """
        if isinstance(message, str):
            formatted_message = message
        else:
            raise TypeError("message must be a string")
        timestamp_value = self._current_timestamp()
        print(f"[{timestamp_value}] {formatted_message}", flush=True)
        return None
