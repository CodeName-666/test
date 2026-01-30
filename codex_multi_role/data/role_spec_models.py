"""Dataclass models for Codex role specifications."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class PromptFlags:
    """Flags that control tool and filesystem instructions for a role.

    Attributes:
        allow_tools: Whether tools/commands are allowed for the role.
        allow_read: Whether reading files is permitted for the role.
        allow_write: Whether writing files is permitted for the role.
        allow_file_suggestions: Whether file change suggestions are allowed.
    """

    allow_tools: bool = True
    allow_read: bool = True
    allow_write: bool = False
    allow_file_suggestions: bool = False

    def __post_init__(self) -> None:
        """Validate prompt flag fields after initialization.

        Raises:
            TypeError: If any flag value is not a boolean.
        """
        self._validate_flag(self.allow_tools, "allow_tools")
        self._validate_flag(self.allow_read, "allow_read")
        self._validate_flag(self.allow_write, "allow_write")
        self._validate_flag(self.allow_file_suggestions, "allow_file_suggestions")
        return None

    def _validate_flag(self, value: bool, field_name: str) -> None:
        """Validate a boolean flag field.

        Args:
            value: Value to validate.
            field_name: Field name for error reporting.

        Raises:
            TypeError: If value is not a boolean.
        """
        if isinstance(value, bool):
            pass
        else:
            raise TypeError(f"{field_name} must be a boolean")
        return None


@dataclass(frozen=True)
class RoleBehaviors:
    """Behavior switches that influence orchestrator handling.

    Attributes:
        timeout_policy: Named timeout policy (for example "planner").
        apply_files: Whether file suggestions should be applied.
        can_finish: Whether the role can signal DONE.
    """

    timeout_policy: str = "default"
    apply_files: bool = False
    can_finish: bool = False

    def __post_init__(self) -> None:
        """Validate behavior fields after initialization.

        Raises:
            TypeError: If field types are invalid.
            ValueError: If timeout_policy is empty.
        """
        self._validate_timeout_policy()
        self._validate_flag(self.apply_files, "apply_files")
        self._validate_flag(self.can_finish, "can_finish")
        return None

    def _validate_timeout_policy(self) -> None:
        """Validate the timeout_policy field."""
        if isinstance(self.timeout_policy, str):
            if self.timeout_policy.strip():
                pass
            else:
                raise ValueError("timeout_policy must not be empty")
        else:
            raise TypeError("timeout_policy must be a string")
        return None

    def _validate_flag(self, value: bool, field_name: str) -> None:
        """Validate a boolean flag field.

        Args:
            value: Value to validate.
            field_name: Field name for error reporting.

        Raises:
            TypeError: If value is not a boolean.
        """
        if isinstance(value, bool):
            pass
        else:
            raise TypeError(f"{field_name} must be a boolean")
        return None


@dataclass
class RoleSpec:
    """Runtime specification for a single role.

    Attributes:
        name: Unique role name used in orchestration.
        model: Model name or alias to request from the backend.
        reasoning_effort: Optional reasoning effort label for the model.
        system_instructions: Base prompt text for the role.
        prompt_flags: Flags used to generate capability rules.
        behaviors: Orchestrator behaviors tied to the role.
    """

    name: str
    model: str
    reasoning_effort: Optional[str]
    system_instructions: str
    prompt_flags: PromptFlags = field(default_factory=PromptFlags)
    behaviors: RoleBehaviors = field(default_factory=RoleBehaviors)

    def __post_init__(self) -> None:
        """Validate role specification fields after initialization.

        Raises:
            TypeError: If field types are invalid.
            ValueError: If required string fields are empty.
        """
        self._validate_name()
        self._validate_model()
        self._validate_reasoning_effort()
        self._validate_system_instructions()
        self._validate_prompt_flags()
        self._validate_behaviors()
        return None

    def _validate_name(self) -> None:
        """Validate the role name."""
        if isinstance(self.name, str):
            if self.name.strip():
                pass
            else:
                raise ValueError("name must not be empty")
        else:
            raise TypeError("name must be a string")
        return None

    def _validate_model(self) -> None:
        """Validate the model field."""
        if isinstance(self.model, str):
            if self.model.strip():
                pass
            else:
                raise ValueError("model must not be empty")
        else:
            raise TypeError("model must be a string")
        return None

    def _validate_reasoning_effort(self) -> None:
        """Validate the reasoning_effort field."""
        if self.reasoning_effort is None:
            pass
        elif isinstance(self.reasoning_effort, str):
            if self.reasoning_effort.strip():
                pass
            else:
                raise ValueError("reasoning_effort must not be empty")
        else:
            raise TypeError("reasoning_effort must be a string or None")
        return None

    def _validate_system_instructions(self) -> None:
        """Validate the system_instructions field."""
        if isinstance(self.system_instructions, str):
            if self.system_instructions.strip():
                pass
            else:
                raise ValueError("system_instructions must not be empty")
        else:
            raise TypeError("system_instructions must be a string")
        return None

    def _validate_prompt_flags(self) -> None:
        """Validate the prompt_flags field."""
        if isinstance(self.prompt_flags, PromptFlags):
            pass
        else:
            raise TypeError("prompt_flags must be a PromptFlags instance")
        return None

    def _validate_behaviors(self) -> None:
        """Validate the behaviors field."""
        if isinstance(self.behaviors, RoleBehaviors):
            pass
        else:
            raise TypeError("behaviors must be a RoleBehaviors instance")
        return None
