"""YAML loaders for role configuration files."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

import yaml


class RoleYamlLoader:
    """Load YAML-based role configuration files.

    Args:
        config_path: Path to the main role configuration YAML.
    """

    def __init__(self, config_path: Path) -> None:
        """Initialize the loader with the main config path.

        Args:
            config_path: Path to the roles YAML file.

        Raises:
            TypeError: If config_path is not a Path.
        """
        if isinstance(config_path, Path):
            self._config_path = config_path
        else:
            raise TypeError("config_path must be a pathlib.Path")
        self._config_dir = self._config_path.parent

    def load_config(self) -> Dict[str, Any]:
        """Load the main YAML configuration file.

        Returns:
            Parsed configuration as a dictionary.

        Raises:
            FileNotFoundError: If the config file does not exist.
            ValueError: If the config file does not parse to a mapping.
        """
        config_data: Dict[str, Any] = {}
        if self._config_path.is_file():
            raw_text = self._config_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(raw_text)
            if parsed is None:
                parsed = {}
            config_data = dict(self._ensure_mapping(parsed, "role config"))
        else:
            raise FileNotFoundError(f"Role config not found: {self._config_path}")
        result = config_data
        return result

    def load_role_file(self, role_value: str) -> Mapping[str, Any]:
        """Load role configuration from a role_file reference.

        Args:
            role_value: Role file path as a string.

        Returns:
            Parsed role configuration mapping.

        Raises:
            FileNotFoundError: If the role file does not exist.
            TypeError: If role_value is not a string.
            ValueError: If role_value is empty or the file does not parse to a mapping.
        """
        if isinstance(role_value, str):
            normalized = role_value.strip()
            if not normalized:
                raise ValueError("role_file must not be empty")
        else:
            raise TypeError("role_file must be a string")

        role_path = self._resolve_role_path(normalized)
        if role_path.is_file():
            raw_text = role_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(raw_text)
            if parsed is None:
                parsed = {}
            role_config = self._ensure_mapping(parsed, f"role_file: {role_path}")
        else:
            raise FileNotFoundError(f"Role file not found: {role_path}")
        result = role_config
        return result

    def _resolve_role_path(self, role_value: str) -> Path:
        """Resolve a role config file path relative to the config directory.

        Args:
            role_value: Role file path, absolute or relative.

        Returns:
            Resolved absolute path to the role config file.
        """
        base_path = Path(role_value)
        resolved_path = base_path
        if base_path.is_absolute():
            resolved_path = base_path
        else:
            resolved_path = (self._config_dir / base_path).resolve()
        result = resolved_path
        return result

    def _ensure_mapping(self, value: Any, context: str) -> Mapping[str, Any]:
        """Validate that a value is a mapping.

        Args:
            value: Value to validate.
            context: Context label for error messages.

        Returns:
            The same value, typed as a mapping.

        Raises:
            ValueError: If value is not a mapping.
        """
        result: Mapping[str, Any]
        if isinstance(value, Mapping):
            result = value
        else:
            raise ValueError(f"{context} must be a mapping")
        return result
