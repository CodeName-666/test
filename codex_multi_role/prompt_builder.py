"""Prompt construction helpers for the Codex orchestrator."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .roles.data.role_spec_models import RoleSpec
from .roles.role_spec import RoleSpecCatalog
from .utils.json_utils import JsonPayloadFormatter


class PromptBuilder:
    """Build prompts for role turns and strict JSON repair requests."""

    def __init__(
        self,
        role_spec_catalog: RoleSpecCatalog,
        json_formatter: JsonPayloadFormatter,
        role_specs_by_name: Dict[str, RoleSpec],
        goal: str,
    ) -> None:
        """Initialize the prompt builder.

        Args:
            role_spec_catalog: Catalog used for prompt formatting and schema hints.
            json_formatter: Formatter for serializing incoming payloads.
            role_specs_by_name: Mapping of role names to role specifications.
            goal: Overall run goal used in role prompts.

        Raises:
            TypeError: If any argument has an invalid type.
        """
        if isinstance(role_spec_catalog, RoleSpecCatalog):
            self._role_spec_catalog = role_spec_catalog
        else:
            raise TypeError("role_spec_catalog must be a RoleSpecCatalog")

        if isinstance(json_formatter, JsonPayloadFormatter):
            self._json_formatter = json_formatter
        else:
            raise TypeError("json_formatter must be a JsonPayloadFormatter")

        if isinstance(role_specs_by_name, dict):
            self._role_specs_by_name = role_specs_by_name
        else:
            raise TypeError("role_specs_by_name must be a dict")

        if isinstance(goal, str):
            self._goal = goal
        else:
            raise TypeError("goal must be a string")

    def _build_prompt(
        self,
        role_name: str,
        incoming: Optional[Dict[str, Any]],
    ) -> str:
        """Construct the prompt that is sent to a specific role.

        Args:
            role_name: Role name to build a prompt for.
            incoming: Optional incoming payload from the previous role.

        Returns:
            Rendered prompt string for the role.

        Raises:
            TypeError: If role_name is not a string or incoming is not a dict/None.
            ValueError: If role_name is empty.
            KeyError: If role_name is not configured.
        """
        normalized_role = self._normalize_role_name(role_name)
        incoming_payload = self._normalize_incoming_payload(incoming)
        specification = self._get_role_specification(normalized_role)

        prompt_parts = [
            self._role_spec_catalog.format_general_prompt(
                "role_header",
                role_name=normalized_role,
            ),
            f"{specification.system_instructions}\n\n",
            self._role_spec_catalog.format_general_prompt(
                "goal_section",
                goal=self._goal,
            ),
        ]
        if incoming_payload:
            prompt_parts.append(
                self._role_spec_catalog.format_general_prompt(
                    "input_section",
                    input=self._json_formatter.normalize_json(incoming_payload),
                )
            )
        prompt_parts.append(self._role_spec_catalog.json_contract_instruction())
        prompt_parts.append(
            self._role_spec_catalog.schema_hint_non_json(normalized_role)
        )
        prompt_parts.append(
            self._role_spec_catalog.format_general_prompt("rules_header")
        )
        prompt_parts.append(
            self._role_spec_catalog.capability_rules(specification.prompt_flags)
        )
        prompt_parts.append(
            self._role_spec_catalog.format_general_prompt("analysis_rules")
        )
        result = "".join(prompt_parts)
        return result

    def _build_repair_prompt(self, issue_description: str) -> str:
        """Build a strict JSON-only repair prompt when parsing fails.

        Args:
            issue_description: Description of the JSON parsing failure.

        Returns:
            Repair prompt text to request a strict JSON response.

        Raises:
            TypeError: If issue_description is not a string.
            ValueError: If issue_description is empty.
        """
        if isinstance(issue_description, str):
            if issue_description.strip():
                description = issue_description
            else:
                raise ValueError("issue_description must not be empty")
        else:
            raise TypeError("issue_description must be a string")
        repair_prompt = (
            f"{description}\n"
            "Bitte liefere GENAU EIN JSON-Objekt und sonst nichts.\n"
            + self._role_spec_catalog.json_contract_instruction()
        )
        result = repair_prompt
        return result

    def _normalize_role_name(self, role_name: str) -> str:
        if not isinstance(role_name, str):
            raise TypeError("role_name must be a string")
        if not role_name.strip():
            raise ValueError("role_name must not be empty")
        result = role_name
        return result

    def _normalize_incoming_payload(
        self,
        incoming: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if incoming is None:
            incoming_payload = None
        elif not isinstance(incoming, dict):
            raise TypeError("incoming must be a dict or None")
        else:
            incoming_payload = incoming
        result = incoming_payload
        return result

    def _get_role_specification(self, role_name: str) -> RoleSpec:
        if role_name in self._role_specs_by_name:
            specification = self._role_specs_by_name[role_name]
        else:
            raise KeyError(f"role not configured: {role_name}")
        result = specification
        return result
