"""Agent registry, capability validation, and payload redaction utilities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..roles.role_spec import RoleSpec
from .delegation_manager import Delegation


SECRET_KEYWORDS = (
    "secret",
    "token",
    "password",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "credential",
)


@dataclass(frozen=True)
class AgentPolicy:
    """Static policy metadata for an agent."""

    agent_id: str
    capabilities: List[str]
    allowed_tools: List[str]
    risk_level: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize policy to dictionary."""
        result = {
            "agent_id": self.agent_id,
            "capabilities": list(self.capabilities),
            "allowed_tools": list(self.allowed_tools),
            "risk_level": self.risk_level,
        }
        return result


class AgentRegistry:
    """Registry of agent policies derived from role specifications."""

    def __init__(self, policies: Dict[str, AgentPolicy]) -> None:
        """Initialize registry.

        Args:
            policies: Mapping of agent ids to policies.
        """
        if not isinstance(policies, dict):
            raise TypeError("policies must be a dictionary")
        self._policies = policies

    @classmethod
    def from_role_specs(
        cls,
        role_specs: Dict[str, RoleSpec],
    ) -> "AgentRegistry":
        """Build registry from role specifications.

        Args:
            role_specs: Mapping of role names to RoleSpec instances.

        Returns:
            Registry with one policy per role.
        """
        if not isinstance(role_specs, dict):
            raise TypeError("role_specs must be a dictionary")
        policies: Dict[str, AgentPolicy] = {}
        for role_name, role_spec in role_specs.items():
            if not isinstance(role_name, str):
                raise TypeError("role name keys must be strings")
            if not isinstance(role_spec, RoleSpec):
                raise TypeError("role spec values must be RoleSpec instances")
            allowed_tools: List[str] = []
            if role_spec.prompt_flags.allow_tools:
                if role_spec.prompt_flags.allow_read:
                    allowed_tools.append("read")
                if role_spec.prompt_flags.allow_write:
                    allowed_tools.append("write")
                if role_spec.prompt_flags.allow_file_suggestions:
                    allowed_tools.append("file_suggestions")
            risk_level = "low"
            if role_spec.prompt_flags.allow_write:
                risk_level = "high"
            elif role_spec.prompt_flags.allow_tools:
                risk_level = "medium"
            policies[role_name] = AgentPolicy(
                agent_id=role_name,
                capabilities=[role_name],
                allowed_tools=allowed_tools,
                risk_level=risk_level,
            )
        result = cls(policies=policies)
        return result

    def has_agent(self, agent_id: str) -> bool:
        """Check if an agent is registered."""
        result = agent_id in self._policies
        return result

    def get_policy(self, agent_id: str) -> Optional[AgentPolicy]:
        """Get a policy by agent id."""
        result = self._policies.get(agent_id)
        return result

    def validate_delegation(self, delegation: Delegation) -> List[str]:
        """Validate delegation against policy registry.

        Args:
            delegation: Delegation that is about to be executed.

        Returns:
            List of validation errors. Empty list means valid.
        """
        errors: List[str] = []
        if delegation.agent_id not in self._policies:
            errors.append(f"unknown agent_id '{delegation.agent_id}'")
        else:
            policy = self._policies[delegation.agent_id]
            required_capabilities = delegation.context.get("required_capabilities", [])
            requested_tools = delegation.context.get("requested_tools", [])
            if isinstance(required_capabilities, list):
                for capability in required_capabilities:
                    if isinstance(capability, str):
                        if capability not in policy.capabilities:
                            errors.append(
                                f"agent '{delegation.agent_id}' lacks capability '{capability}'"
                            )
                    else:
                        errors.append(
                            f"required_capabilities contains non-string value for delegation '{delegation.delegation_id}'"
                        )
            elif required_capabilities is not None:
                errors.append(
                    f"required_capabilities must be a list in delegation '{delegation.delegation_id}'"
                )
            if isinstance(requested_tools, list):
                for tool_name in requested_tools:
                    if isinstance(tool_name, str):
                        if tool_name not in policy.allowed_tools:
                            errors.append(
                                f"agent '{delegation.agent_id}' is not allowed to use tool '{tool_name}'"
                            )
                    else:
                        errors.append(
                            f"requested_tools contains non-string value in delegation '{delegation.delegation_id}'"
                        )
            elif requested_tools is not None:
                errors.append(
                    f"requested_tools must be a list in delegation '{delegation.delegation_id}'"
                )
        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Serialize registry policies."""
        result = {
            agent_id: policy.to_dict() for agent_id, policy in self._policies.items()
        }
        return result


def redact_secrets(value: Any) -> Any:
    """Redact secret values recursively before persistence.

    Args:
        value: JSON-like value to sanitize.

    Returns:
        Redacted copy of the value.
    """
    if isinstance(value, dict):
        redacted_dict: Dict[str, Any] = {}
        for key, nested_value in value.items():
            if _looks_sensitive_key(key):
                redacted_dict[key] = "***REDACTED***"
            else:
                redacted_dict[key] = redact_secrets(nested_value)
        result: Any = redacted_dict
    elif isinstance(value, list):
        redacted_list = [redact_secrets(item) for item in value]
        result = redacted_list
    elif isinstance(value, str):
        stripped = value.strip()
        if _looks_sensitive_value(stripped):
            result = "***REDACTED***"
        else:
            result = value
    else:
        result = value
    return result


def _looks_sensitive_key(key: Any) -> bool:
    looks_sensitive = False
    if isinstance(key, str):
        normalized = key.strip().lower()
        looks_sensitive = any(keyword in normalized for keyword in SECRET_KEYWORDS)
    return looks_sensitive


def _looks_sensitive_value(value: str) -> bool:
    normalized = value.lower()
    looks_like_token = normalized.startswith("sk-")
    looks_like_bearer = normalized.startswith("bearer ")
    contains_private_key = "-----begin " in normalized and "private key-----" in normalized
    result = looks_like_token or looks_like_bearer or contains_private_key
    return result
