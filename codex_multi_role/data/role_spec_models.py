"""Dataclass models for Codex role specifications."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..validation_utils import ValidationMixin


@dataclass(frozen=True)
class PromptFlags(ValidationMixin):
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
        self._validate_bool(self.allow_tools, "allow_tools")
        self._validate_bool(self.allow_read, "allow_read")
        self._validate_bool(self.allow_write, "allow_write")
        self._validate_bool(self.allow_file_suggestions, "allow_file_suggestions")
        return None


@dataclass(frozen=True)
class RoleBehaviors(ValidationMixin):
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
        self._validate_non_empty_str(self.timeout_policy, "timeout_policy")
        self._validate_bool(self.apply_files, "apply_files")
        self._validate_bool(self.can_finish, "can_finish")
        return None


@dataclass
class RoleSpec(ValidationMixin):
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
        self._validate_non_empty_str(self.name, "name")
        self._validate_non_empty_str(self.model, "model")
        self._validate_optional_non_empty_str(self.reasoning_effort, "reasoning_effort")
        self._validate_non_empty_str(self.system_instructions, "system_instructions")
        self._validate_instance(self.prompt_flags, PromptFlags, "prompt_flags")
        self._validate_instance(self.behaviors, RoleBehaviors, "behaviors")
        return None
