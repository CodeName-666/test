"""Specifications for each Codex role (planner / architect / implementer / integrator)."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Type

import yaml

from defaults import DEFAULT_ENVIRONMENT
from .env_utils import EnvironmentReader
from .data.role_spec_models import PromptFlags, RoleBehaviors, RoleSpec


def _defaults() -> Any:
    import defaults

    result = defaults
    return result


class RoleSpecCatalog:
    """Build role specifications and JSON schema hints from a YAML config."""

    def __init__(
        self,
        environment_reader: EnvironmentReader = DEFAULT_ENVIRONMENT,
        config_path: Optional[Path] = None,
    ) -> None:
        """Initialize the catalog and load the YAML configuration.

        Args:
            environment_reader: Reader for environment variables. Must be an
                EnvironmentReader instance.
            config_path: Optional explicit path to the roles YAML file. When None,
                the default ROLE_CONFIG_PATH/ROLE_CONFIG_FILENAME resolution is used.

        Raises:
            FileNotFoundError: If the roles YAML file does not exist.
            TypeError: If config_path has an invalid type.
            ValueError: If the YAML structure is invalid.
        """
        defaults = _defaults()
        self._environment_reader = environment_reader
        resolved_path = self._resolve_config_path(config_path)
        self._config_path = resolved_path
        self._config = self._load_config()
        self._general_prompts = self._ensure_mapping(
            self._config.get(defaults.CONFIG_KEY_GENERAL_PROMPTS) or {},
            defaults.CONFIG_KEY_GENERAL_PROMPTS,
        )
        self._schema_hints = self._ensure_mapping(
            self._config.get(defaults.CONFIG_KEY_SCHEMA_HINTS) or {},
            defaults.CONFIG_KEY_SCHEMA_HINTS,
        )
        self._defaults = self._ensure_mapping(
            self._config.get(defaults.CONFIG_KEY_DEFAULTS) or {},
            defaults.CONFIG_KEY_DEFAULTS,
        )
        return None

    def get_default_model_name(self) -> str:
        """Return the default model name from environment or fallback.

        Returns:
            Default model name used when a role does not specify a model env var.
        """
        defaults = _defaults()
        default_model_name = self._environment_reader.get_str(
            defaults.DEFAULT_MODEL_ENV,
            defaults.DEFAULT_MODEL_NAME,
        )
        result = default_model_name
        return result

    def _resolve_config_path(self, config_path: Optional[Path]) -> Path:
        """Resolve the roles config path with an environment override.

        Args:
            config_path: Optional explicit path to the roles YAML file.

        Returns:
            Resolved filesystem path to the YAML config.

        Raises:
            TypeError: If config_path is not a Path or None.
        """
        defaults = _defaults()
        resolved_path = Path(__file__)
        if config_path is not None and not isinstance(config_path, Path):
            raise TypeError("config_path must be a pathlib.Path or None")

        env_path = self._environment_reader.get_str(defaults.ROLE_CONFIG_ENV, "")
        if env_path:
            resolved_path = Path(env_path)
        elif config_path is not None:
            resolved_path = config_path
        else:
            resolved_path = (
                Path(__file__).resolve().parent.parent
                / defaults.ROLE_CONFIG_DIRECTORY
                / defaults.ROLE_CONFIG_FILENAME
            )

        resolved_path = resolved_path.resolve()
        return resolved_path

    def _load_config(self) -> Dict[str, Any]:
        """Load and validate the YAML configuration file.

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
            if isinstance(parsed, dict):
                config_data = parsed
            else:
                raise ValueError(f"Role config must be a mapping: {self._config_path}")
        else:
            raise FileNotFoundError(f"Role config not found: {self._config_path}")
        return config_data

    def _ensure_mapping(self, value: Any, context: str) -> Mapping[str, Any]:
        """Validate that a value is a mapping.

        Args:
            value: Value to validate.
            context: Context label for error messages.

        Returns:
            The same value, typed as a mapping.

        Raises:
            TypeError: If value is not a mapping.
        """
        result: Mapping[str, Any]
        if isinstance(value, Mapping):
            result = value
        else:
            raise TypeError(f"{context} must be a mapping")
        return result

    def _require_non_empty_str(self, value: Any, context: str) -> str:
        """Validate that a value is a non-empty string.

        Args:
            value: Value to validate.
            context: Context label for error messages.

        Returns:
            Stripped, non-empty string.

        Raises:
            TypeError: If value is not a string.
            ValueError: If value is empty after stripping.
        """
        result = ""
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                result = stripped
            else:
                raise ValueError(f"{context} must not be empty")
        else:
            raise TypeError(f"{context} must be a string")
        return result

    def _normalize_optional_str(self, value: Any, context: str) -> str:
        """Normalize an optional string value by stripping whitespace.

        Args:
            value: Value to normalize.
            context: Context label for error messages.

        Returns:
            Stripped string, or an empty string if the value is None.

        Raises:
            TypeError: If value is not None and not a string.
        """
        result = ""
        if value is None:
            result = ""
        elif isinstance(value, str):
            result = value.strip()
        else:
            raise TypeError(f"{context} must be a string or None")
        return result

    def _resolve_prompt_path(self, prompt_value: str) -> Path:
        """Resolve a prompt file path relative to the config directory.

        Args:
            prompt_value: Prompt file path, absolute or relative.

        Returns:
            Resolved absolute path to the prompt file.
        """
        base_path = Path(prompt_value)
        resolved_path = base_path
        if base_path.is_absolute():
            resolved_path = base_path
        else:
            resolved_path = (self._config_path.parent / base_path).resolve()
        return resolved_path

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
            resolved_path = (self._config_path.parent / base_path).resolve()
        return resolved_path

    def _load_role_file(self, role_value: str) -> Mapping[str, Any]:
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
        defaults = _defaults()
        normalized = self._normalize_optional_str(role_value, defaults.CONFIG_KEY_ROLE_FILE)
        role_config: Mapping[str, Any] = {}
        if normalized:
            role_path = self._resolve_role_path(normalized)
            if role_path.is_file():
                raw_text = role_path.read_text(encoding="utf-8")
                parsed = yaml.safe_load(raw_text)
                if parsed is None:
                    parsed = {}
                if isinstance(parsed, Mapping):
                    role_config = parsed
                else:
                    raise ValueError(
                        f"{defaults.CONFIG_KEY_ROLE_FILE} must be a mapping: {role_path}"
                    )
            else:
                raise FileNotFoundError(f"Role file not found: {role_path}")
        else:
            raise ValueError("role_file must not be empty")
        result = role_config
        return result

    def _load_prompt(self, prompt_value: str) -> str:
        """Load prompt text from a file path.

        Args:
            prompt_value: Prompt file path as a string.

        Returns:
            Prompt text content.

        Raises:
            FileNotFoundError: If the prompt file does not exist.
            ValueError: If prompt_value is empty.
            TypeError: If prompt_value is not a string.
        """
        defaults = _defaults()
        prompt_text = ""
        normalized = self._normalize_optional_str(prompt_value, defaults.CONFIG_KEY_PROMPT_FILE)
        if normalized:
            prompt_path = self._resolve_prompt_path(normalized)
            if prompt_path.is_file():
                prompt_text = prompt_path.read_text(encoding="utf-8").strip()
            else:
                raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
        else:
            raise ValueError("prompt_file must not be empty")
        return prompt_text

    def _coerce_dataclass(
        self,
        data: Mapping[str, Any],
        default_instance: Any,
        dataclass_type: Type[Any],
        context: str,
    ) -> Any:
        """Create a dataclass instance by overlaying values on defaults.

        Args:
            data: Mapping of override values.
            default_instance: Default instance for baseline values.
            dataclass_type: Dataclass type to instantiate.
            context: Context label for error messages.

        Returns:
            Instantiated dataclass of the given type.

        Raises:
            TypeError: If data is not a mapping or dataclass_type is invalid.
        """
        if not isinstance(data, Mapping):
            raise TypeError(f"{context} must be a mapping")
        if not is_dataclass(dataclass_type):
            raise TypeError(f"{context} requires a dataclass type")

        values: Dict[str, Any] = {
            field_item.name: getattr(default_instance, field_item.name)
            for field_item in fields(dataclass_type)
        }
        for field_item in fields(dataclass_type):
            if field_item.name in data:
                values[field_item.name] = data[field_item.name]

        result = dataclass_type(**values)
        return result

    def _extract_role_name(self, role_config: Mapping[str, Any]) -> str:
        """Extract and validate the role name."""
        defaults = _defaults()
        name_value = role_config.get(defaults.CONFIG_KEY_NAME)
        role_name = self._require_non_empty_str(name_value, "roles[].name")
        result = role_name
        return result

    def _validate_role_name_match(
        self,
        role_config: Mapping[str, Any],
        file_config: Mapping[str, Any],
        role_index: int,
    ) -> None:
        """Ensure role name matches between a role entry and role_file."""
        defaults = _defaults()
        role_name_value = role_config.get(defaults.CONFIG_KEY_NAME)
        file_name_value = file_config.get(defaults.CONFIG_KEY_NAME)
        if role_name_value is not None and file_name_value is not None:
            role_name = self._require_non_empty_str(
                role_name_value,
                f"roles[{role_index}].{defaults.CONFIG_KEY_NAME}",
            )
            file_name = self._require_non_empty_str(
                file_name_value,
                f"role_file[{role_index}].{defaults.CONFIG_KEY_NAME}",
            )
            if role_name != file_name:
                raise ValueError(
                    "Role name mismatch between roles entry and role_file: "
                    f"{role_name} != {file_name}"
                )
        return None

    def _resolve_role_entry(
        self,
        role_config: Mapping[str, Any],
        role_index: int,
    ) -> Mapping[str, Any]:
        """Resolve role configuration, loading role_file when provided.

        Args:
            role_config: Role configuration mapping from the roles list.
            role_index: Index of the role entry for error messages.

        Returns:
            Resolved role configuration mapping.

        Raises:
            FileNotFoundError: If a referenced role file is missing.
            TypeError: If role_file has an invalid type.
            ValueError: If role_file is empty or parses to an invalid mapping.
        """
        defaults = _defaults()
        role_file_value = role_config.get(defaults.CONFIG_KEY_ROLE_FILE)
        role_file = self._normalize_optional_str(
            role_file_value,
            f"roles[{role_index}].{defaults.CONFIG_KEY_ROLE_FILE}",
        )
        resolved_config: Mapping[str, Any] = role_config
        if role_file:
            file_config = self._load_role_file(role_file)
            merged_config = {**file_config, **role_config}
            if defaults.CONFIG_KEY_ROLE_FILE in merged_config:
                merged_config = dict(merged_config)
                merged_config.pop(defaults.CONFIG_KEY_ROLE_FILE, None)
            self._validate_role_name_match(role_config, file_config, role_index)
            resolved_config = merged_config
        else:
            resolved_config = role_config
        result = resolved_config
        return result

    def _resolve_model_value(
        self,
        role_name: str,
        role_config: Mapping[str, Any],
        default_model_name: str,
    ) -> str:
        """Resolve the model value using explicit config or env indirection."""
        defaults = _defaults()
        explicit_model_raw = role_config.get(defaults.CONFIG_KEY_MODEL)
        explicit_model = self._normalize_optional_str(
            explicit_model_raw,
            f"roles[{role_name}].{defaults.CONFIG_KEY_MODEL}",
        )
        model_env_value = role_config.get(defaults.CONFIG_KEY_MODEL_ENV)
        model_env = self._normalize_optional_str(
            model_env_value,
            f"roles[{role_name}].model_env",
        )
        if explicit_model:
            model_value = explicit_model
        elif model_env:
            model_value = self._environment_reader.get_str(model_env, default_model_name)
        else:
            model_value = default_model_name
        result = model_value
        return result

    def _resolve_prompt_text(self, role_name: str, role_config: Mapping[str, Any]) -> str:
        """Resolve prompt text from inline YAML or a prompt file."""
        defaults = _defaults()
        prompt_inline_raw = role_config.get(defaults.CONFIG_KEY_PROMPT_TEXT)
        prompt_file_raw = role_config.get(defaults.CONFIG_KEY_PROMPT_FILE)
        prompt_inline = self._normalize_optional_str(
            prompt_inline_raw,
            f"roles[{role_name}].prompt_text",
        )
        prompt_file = self._normalize_optional_str(
            prompt_file_raw,
            f"roles[{role_name}].prompt_file",
        )

        prompt_text = ""
        if prompt_inline:
            prompt_text = prompt_inline
        elif prompt_file:
            prompt_text = self._load_prompt(prompt_file)
        else:
            raise ValueError(f"Role '{role_name}' missing prompt_text or prompt_file")
        result = prompt_text
        return result

    def _merge_prompt_flags(self, role_name: str, role_config: Mapping[str, Any]) -> PromptFlags:
        """Merge default and role-specific prompt flags."""
        defaults = _defaults()
        defaults_flags = self._ensure_mapping(
            self._defaults.get(defaults.CONFIG_KEY_PROMPT_FLAGS) or {},
            f"{defaults.CONFIG_KEY_DEFAULTS}.{defaults.CONFIG_KEY_PROMPT_FLAGS}",
        )
        role_flags = self._ensure_mapping(
            role_config.get(defaults.CONFIG_KEY_PROMPT_FLAGS) or {},
            f"roles[{role_name}].{defaults.CONFIG_KEY_PROMPT_FLAGS}",
        )
        merged_flags = {**defaults_flags, **role_flags}
        prompt_flags = self._coerce_dataclass(
            merged_flags,
            PromptFlags(),
            PromptFlags,
            f"roles[{role_name}].{defaults.CONFIG_KEY_PROMPT_FLAGS}",
        )
        result = prompt_flags
        return result

    def _merge_behaviors(self, role_name: str, role_config: Mapping[str, Any]) -> RoleBehaviors:
        """Merge default and role-specific behavior settings."""
        defaults = _defaults()
        defaults_behaviors = self._ensure_mapping(
            self._defaults.get(defaults.CONFIG_KEY_BEHAVIORS) or {},
            f"{defaults.CONFIG_KEY_DEFAULTS}.{defaults.CONFIG_KEY_BEHAVIORS}",
        )
        role_behaviors = self._ensure_mapping(
            role_config.get(defaults.CONFIG_KEY_BEHAVIORS) or {},
            f"roles[{role_name}].{defaults.CONFIG_KEY_BEHAVIORS}",
        )
        merged_behaviors = {**defaults_behaviors, **role_behaviors}
        behaviors = self._coerce_dataclass(
            merged_behaviors,
            RoleBehaviors(),
            RoleBehaviors,
            f"roles[{role_name}].{defaults.CONFIG_KEY_BEHAVIORS}",
        )
        result = behaviors
        return result

    def _resolve_reasoning_effort(
        self,
        role_name: str,
        role_config: Mapping[str, Any],
    ) -> Optional[str]:
        """Resolve the reasoning effort with defaults and validation."""
        defaults = _defaults()
        default_raw = self._defaults.get(
            defaults.CONFIG_KEY_REASONING_EFFORT,
            defaults.DEFAULT_REASONING_EFFORT,
        )
        default_value = self._normalize_optional_str(
            default_raw,
            f"{defaults.CONFIG_KEY_DEFAULTS}.{defaults.CONFIG_KEY_REASONING_EFFORT}",
        )
        if not default_value:
            default_value = defaults.DEFAULT_REASONING_EFFORT

        raw_value = role_config.get(defaults.CONFIG_KEY_REASONING_EFFORT)
        reasoning_value = ""
        if raw_value is None:
            reasoning_value = default_value
        elif isinstance(raw_value, str):
            stripped = raw_value.strip()
            if stripped:
                reasoning_value = stripped
            else:
                reasoning_value = default_value
        else:
            raise TypeError(
                f"roles[{role_name}].{defaults.CONFIG_KEY_REASONING_EFFORT} must be a string"
            )

        result: Optional[str] = reasoning_value
        return result

    def _build_role(
        self,
        role_config: Mapping[str, Any],
        default_model_name: str,
    ) -> RoleSpec:
        """Build a RoleSpec instance from a role config mapping.

        Args:
            role_config: Mapping containing role configuration values.
            default_model_name: Default model name to use when none is provided.

        Returns:
            RoleSpec for the role.

        Raises:
            ValueError: If required fields are missing.
            TypeError: If field types are invalid.
            FileNotFoundError: If a referenced prompt file is missing.
        """
        role_name = self._extract_role_name(role_config)
        model_value = self._resolve_model_value(role_name, role_config, default_model_name)
        prompt_text = self._resolve_prompt_text(role_name, role_config)
        prompt_flags = self._merge_prompt_flags(role_name, role_config)
        behaviors = self._merge_behaviors(role_name, role_config)
        reasoning_value = self._resolve_reasoning_effort(role_name, role_config)

        role_spec = RoleSpec(
            name=role_name,
            model=model_value,
            reasoning_effort=reasoning_value,
            system_instructions=prompt_text,
            prompt_flags=prompt_flags,
            behaviors=behaviors,
        )
        result = role_spec
        return result

    def build_role_specs(self) -> List[RoleSpec]:
        """Build RoleSpec objects for all configured roles.

        Returns:
            List of role specifications in configured order.

        Raises:
            ValueError: If the roles section is missing or invalid.
            TypeError: If any role entry is not a mapping.
        """
        defaults = _defaults()
        default_model_name = self.get_default_model_name()
        roles_value = self._config.get(defaults.CONFIG_KEY_ROLES)
        roles_config: List[Any] = []
        if roles_value is None:
            raise ValueError("Role config must contain a roles list")
        elif isinstance(roles_value, list):
            roles_config = roles_value
        else:
            raise ValueError("Role config must contain a roles list")

        role_specs: List[RoleSpec] = []
        for index, role_config in enumerate(roles_config):
            if isinstance(role_config, Mapping):
                resolved_config = self._resolve_role_entry(role_config, index)
                role_specs.append(self._build_role(resolved_config, default_model_name))
            else:
                raise TypeError(f"roles[{index}] must be a mapping")

        result = role_specs
        return result

    def _format_block(self, text: str, prefix_newline: bool = True) -> str:
        """Format a text block with optional leading newline.

        Args:
            text: Text to format.
            prefix_newline: Whether to prefix with a newline.

        Returns:
            Formatted block or empty string when text is blank.
        """
        cleaned = (text or "").strip()
        result = ""
        if cleaned:
            if prefix_newline:
                result = f"\n{cleaned}\n"
            else:
                result = f"{cleaned}\n"
        else:
            result = ""
        return result

    def format_general_prompt(self, key: str, **kwargs: Any) -> str:
        """Format a general prompt template from configuration.

        Args:
            key: Template key in the general_prompts section.
            **kwargs: Format values for the template.

        Returns:
            Formatted prompt text or empty string if the key is absent.

        Raises:
            TypeError: If key is not a string.
            ValueError: If template formatting fails.
        """
        key_value = self._require_non_empty_str(key, "general_prompts key")
        template_value = self._general_prompts.get(key_value)
        result = ""
        if template_value is None:
            result = ""
        else:
            try:
                result = str(template_value).format(**kwargs)
            except Exception as exc:
                raise ValueError(f"Failed to format prompt '{key_value}': {exc}") from exc
        return result

    def _ensure_prompt_flags(self, prompt_flags: PromptFlags) -> PromptFlags:
        """Validate prompt flags type."""
        result = prompt_flags
        if isinstance(prompt_flags, PromptFlags):
            result = prompt_flags
        else:
            raise TypeError("prompt_flags must be a PromptFlags instance")
        return result

    def _tools_rule(self, prompt_flags: PromptFlags) -> str:
        """Build the tools permission rule line."""
        result = ""
        if prompt_flags.allow_tools:
            result = "- Tools/Commands sind erlaubt."
        else:
            result = "- Tools/Commands sind NICHT erlaubt."
        return result

    def _file_access_rule(self, prompt_flags: PromptFlags) -> str:
        """Build the file access permission rule line."""
        result = ""
        if prompt_flags.allow_read and prompt_flags.allow_write:
            result = "- Du darfst Dateien LESEN und SCHREIBEN."
        elif prompt_flags.allow_read:
            result = "- Du darfst Dateien NUR LESEN, NICHT schreiben."
        elif prompt_flags.allow_write:
            result = "- Du darfst Dateien schreiben, aber nicht lesen."
        else:
            result = "- Du darfst Dateien NICHT lesen oder schreiben."
        return result

    def _file_suggestion_rule(self, prompt_flags: PromptFlags) -> str:
        """Build the file suggestion rule line if enabled."""
        result = ""
        if prompt_flags.allow_file_suggestions:
            result = (
                "- Gib Dateiänderungen ausschließlich als Vorschlag im Feld "
                "files=[{path,content}] zurück."
            )
        else:
            result = ""
        return result

    def capability_rules(self, prompt_flags: PromptFlags) -> str:
        """Build capability rule lines for a role.

        Args:
            prompt_flags: Flags that control the rule output.

        Returns:
            Formatted rules with trailing newline or empty string.

        Raises:
            TypeError: If prompt_flags is not a PromptFlags instance.
        """
        validated_flags = self._ensure_prompt_flags(prompt_flags)
        lines: List[str] = [
            self._tools_rule(validated_flags),
            self._file_access_rule(validated_flags),
        ]
        file_suggestion_rule = self._file_suggestion_rule(validated_flags)
        if file_suggestion_rule:
            lines.append(file_suggestion_rule)

        result = ""
        if lines:
            result = "\n".join(lines) + "\n"
        else:
            result = ""
        return result

    def json_contract_instruction(self) -> str:
        """Return the JSON-only contract block from configuration.

        Returns:
            Formatted JSON contract block or empty string.
        """
        defaults = _defaults()
        contract_value = self._general_prompts.get(defaults.GENERAL_PROMPT_JSON_CONTRACT)
        contract_text = "" if contract_value is None else str(contract_value)
        result = self._format_block(contract_text, prefix_newline=True)
        return result

    def schema_hint_non_json(self, role_name: str) -> str:
        """Return the schema hint block for a role.

        Args:
            role_name: Role name for template selection.

        Returns:
            Formatted schema hint block or empty string.

        Raises:
            TypeError: If role_name is not a string.
            ValueError: If role_name is empty or formatting fails.
        """
        role_value = self._require_non_empty_str(role_name, "role_name")
        schema_template = self._schema_hints.get(role_value)
        if schema_template is None:
            defaults = _defaults()
            schema_template = self._schema_hints.get(defaults.SCHEMA_HINT_DEFAULT_KEY)

        result = ""
        if schema_template is None:
            result = ""
        else:
            try:
                formatted = str(schema_template).format(role_name=role_value)
            except Exception as exc:
                raise ValueError(
                    f"Failed to format schema hint for role '{role_value}': {exc}"
                ) from exc
            result = self._format_block(formatted, prefix_newline=True)
        return result
