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
