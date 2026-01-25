"""Environment helpers that parse typed CLI/config values."""
from __future__ import annotations

import os
from typing import Mapping, Optional


class EnvironmentReader:
    """Read and convert environment variables with defensive defaults."""

    def __init__(self, environment: Optional[Mapping[str, str]] = None) -> None:
        # Allow dependency injection for tests while defaulting to process env.
        self._environment = environment if environment is not None else os.environ

    def get_int(self, name: str, default: str) -> int:
        """Return an integer environment value or the provided default."""
        raw_value = self._environment.get(name, default)
        normalized_value = (raw_value or "").strip()
        parsed_value = 0
        try:
            parsed_value = int(normalized_value)
        except Exception:
            parsed_value = int(default)
        return parsed_value

    def get_float(self, name: str, default: str) -> float:
        """Return a float environment value or the provided default."""
        raw_value = self._environment.get(name, default)
        normalized_value = (raw_value or "").strip()
        parsed_value = 0.0
        try:
            parsed_value = float(normalized_value)
        except Exception:
            parsed_value = float(default)
        return parsed_value

    def get_flag(self, name: str, default: str = "0") -> bool:
        """Return a boolean flag from common truthy strings."""
        raw_value = self._environment.get(name, default)
        normalized_value = (raw_value or "").strip().lower()
        is_enabled = normalized_value in ("1", "true", "yes", "on")
        return is_enabled

    def get_str(self, name: str, default: str) -> str:
        """Return a trimmed string or the provided default."""
        raw_value = self._environment.get(name)
        normalized_value = ""
        if raw_value is None:
            normalized_value = default
        else:
            normalized_value = raw_value.strip()
            if not normalized_value:
                normalized_value = default
        return normalized_value


DEFAULT_ENVIRONMENT = EnvironmentReader()
