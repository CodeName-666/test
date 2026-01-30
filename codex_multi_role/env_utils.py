"""Environment helpers that parse typed CLI/config values."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml


class EnvironmentReader:
    """Read and convert configuration values with defensive defaults.

    Environment variables override config file values.
    """

    def __init__(
        self,
        environment: Optional[Mapping[str, str]] = None,
        config: Optional[Mapping[str, Any]] = None,
        config_path: Optional[Path] = None,
    ) -> None:
        """Initialize the reader with environment and optional config.

        Args:
            environment: Mapping for environment variables (defaults to os.environ).
            config: Optional config mapping; if omitted, config file is loaded.
            config_path: Optional explicit path to the config YAML.

        Raises:
            FileNotFoundError: If the config file does not exist.
            TypeError: If config_path has an invalid type or config is not a mapping.
            ValueError: If the config YAML does not parse to a mapping.
        """
        resolved_environment = environment if environment is not None else os.environ
        self._environment = resolved_environment
        self._config = self._resolve_config(
            config=config,
            config_path=config_path,
            environment_provided=environment is not None and environment is not os.environ,
        )
        return None

    def get_int(self, name: str, default: str) -> int:
        """Return an integer config value or the provided default."""
        raw_value = self._read_value(name, default)
        normalized_value = raw_value.strip()
        parsed_value = 0
        try:
            parsed_value = int(normalized_value)
        except Exception:
            parsed_value = int(default)
        return parsed_value

    def get_float(self, name: str, default: str) -> float:
        """Return a float config value or the provided default."""
        raw_value = self._read_value(name, default)
        normalized_value = raw_value.strip()
        parsed_value = 0.0
        try:
            parsed_value = float(normalized_value)
        except Exception:
            parsed_value = float(default)
        return parsed_value

    def get_flag(self, name: str, default: str = "0") -> bool:
        """Return a boolean flag from common truthy strings."""
        import defaults

        raw_value = self._read_value(name, default)
        normalized_value = raw_value.strip().lower()
        is_enabled = normalized_value in defaults.TRUTHY_FLAG_VALUES
        result = is_enabled
        return result

    def get_str(self, name: str, default: str) -> str:
        """Return a trimmed string or the provided default."""
        raw_value = self._read_value(name, default)
        normalized_value = raw_value.strip()
        result = ""
        if normalized_value:
            result = normalized_value
        else:
            result = default
        return result

    def apply_defaults_to_environment(self) -> None:
        """Apply config values to the environment for missing keys.

        This updates the environment mapping in-place.
        """
        for key, value in self._config.items():
            if isinstance(key, str):
                existing_value = self._environment.get(key)
                if existing_value is not None and str(existing_value).strip():
                    continue
                if value is None:
                    continue
                normalized_value = ""
                if isinstance(value, str):
                    stripped_value = value.strip()
                    if stripped_value:
                        normalized_value = stripped_value
                    else:
                        continue
                else:
                    normalized_value = str(value)
                self._environment[key] = normalized_value
            else:
                raise TypeError("config keys must be strings")
        return None

    def _resolve_config(
        self,
        config: Optional[Mapping[str, Any]],
        config_path: Optional[Path],
        environment_provided: bool,
    ) -> Mapping[str, Any]:
        """Resolve the config mapping using an optional file path.

        Args:
            config: Optional config mapping override.
            config_path: Optional explicit path to the config YAML.
            environment_provided: True when a custom environment mapping is supplied.

        Returns:
            Mapping with configuration values.

        Raises:
            FileNotFoundError: If config_path is missing and default config is not found.
            TypeError: If config_path has an invalid type or config is not a mapping.
            ValueError: If the config YAML does not parse to a mapping.
        """
        result: Mapping[str, Any]
        if config is None:
            if environment_provided and config_path is None:
                result = {}
            else:
                resolved_path = self._resolve_config_path(config_path)
                result = self._load_config(resolved_path)
        else:
            if isinstance(config, Mapping):
                result = config
            else:
                raise TypeError("config must be a mapping")
        return result

    def _resolve_config_path(self, config_path: Optional[Path]) -> Path:
        """Resolve the config path.

        Args:
            config_path: Optional explicit path to the config YAML.

        Returns:
            Filesystem path to the config YAML.

        Raises:
            TypeError: If config_path is not a pathlib.Path or None.
        """
        import defaults

        resolved_path = Path(__file__)
        if config_path is None:
            resolved_path = (
                Path(__file__).resolve().parent.parent
                / defaults.DEFAULT_CONFIG_DIRECTORY
                / defaults.DEFAULT_CONFIG_FILENAME
            )
        else:
            if isinstance(config_path, Path):
                resolved_path = config_path
            else:
                raise TypeError("config_path must be a pathlib.Path or None")
        resolved_path = resolved_path.resolve()
        return resolved_path

    def _load_config(self, config_path: Path) -> Mapping[str, Any]:
        """Load and validate the YAML configuration file.

        Args:
            config_path: Filesystem path to the config YAML.

        Returns:
            Parsed configuration mapping.

        Raises:
            FileNotFoundError: If the config file does not exist.
            ValueError: If the config file does not parse to a mapping.
        """
        config_data: Mapping[str, Any] = {}
        if config_path.is_file():
            raw_text = config_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(raw_text)
            if parsed is None:
                parsed = {}
            if isinstance(parsed, Mapping):
                config_data = parsed
            else:
                raise ValueError(f"Config must be a mapping: {config_path}")
        else:
            raise FileNotFoundError(f"Config not found: {config_path}")
        return config_data

    def _read_value(self, name: str, default: str) -> str:
        """Return a raw string from environment or config with defaults."""
        raw_env = self._environment.get(name)
        result = ""
        if raw_env is not None and raw_env.strip():
            result = raw_env
        else:
            config_value = self._config.get(name)
            if config_value is None:
                result = default
            else:
                result = self._stringify_config_value(config_value, default)
        return result

    def _stringify_config_value(self, value: Any, default: str) -> str:
        """Normalize a config value into a string, falling back to default."""
        result = ""
        if value is None:
            result = default
        elif isinstance(value, str):
            normalized_value = value.strip()
            if normalized_value:
                result = normalized_value
            else:
                result = default
        else:
            result = str(value)
        return result


def env_int(name: str, default: str) -> int:
    """Return an integer config value via the default environment reader."""
    default_environment = _default_environment()
    result = default_environment.get_int(name, default)
    return result


def env_flag(name: str, default: str = "0") -> bool:
    """Return a boolean config value via the default environment reader."""
    default_environment = _default_environment()
    result = default_environment.get_flag(name, default)
    return result


def env_str(name: str, default: str) -> str:
    """Return a string config value via the default environment reader."""
    default_environment = _default_environment()
    result = default_environment.get_str(name, default)
    return result


def _default_environment() -> "EnvironmentReader":
    from defaults import DEFAULT_ENVIRONMENT

    result = DEFAULT_ENVIRONMENT
    return result
