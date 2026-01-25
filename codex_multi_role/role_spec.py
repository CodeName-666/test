"""Specifications for each Codex role (planner / architect / implementer / integrator)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .env_utils import DEFAULT_ENVIRONMENT, EnvironmentReader


@dataclass
class RoleSpec:
    name: str
    model: str
    reasoning_effort: Optional[str]
    system_instructions: str


class RoleSpecCatalog:
    """Build role specifications and JSON schema hints."""

    def __init__(self, environment_reader: EnvironmentReader = DEFAULT_ENVIRONMENT) -> None:
        self._environment_reader = environment_reader

    def get_default_model_name(self) -> str:
        default_model_name = self._environment_reader.get_str("DEFAULT_MODEL", "gpt-5.1-codex-mini")
        return default_model_name

    def build_role_specs(self) -> List[RoleSpec]:
        default_model_name = self.get_default_model_name()
        role_specs: List[RoleSpec] = [
            RoleSpec(
                name="planner",
                model=self._environment_reader.get_str("PLANNER_MODEL", default_model_name),
                reasoning_effort="high",
                system_instructions=(
                    "Du bist PLANNER. Plane und delegiere. Gib next_owner zurück. "
                    "Tools/Commands sind erlaubt. Du darfst Dateien NUR LESEN, NICHT schreiben. "
                    "Nur JSON, kein Zusatztext."
                ),
            ),
            RoleSpec(
                name="architect",
                model=self._environment_reader.get_str("ARCHITECT_MODEL", default_model_name),
                reasoning_effort="high",
                system_instructions=(
                    "Du bist ARCHITECT. Tools/Commands sind erlaubt. Du darfst Dateien NUR LESEN, "
                    "NICHT schreiben. Tiefe Analyse in analysis_md (Markdown String im JSON). "
                    "Handoff klein halten."
                ),
            ),
            RoleSpec(
                name="implementer",
                model=self._environment_reader.get_str("IMPLEMENTER_MODEL", default_model_name),
                reasoning_effort="high",
                system_instructions=(
                    "Du bist IMPLEMENTER. Tools/Commands sind erlaubt. Du darfst Dateien NUR LESEN, "
                    "NICHT schreiben. Gib Dateiänderungen ausschließlich als Vorschlag im Feld "
                    "files=[{path,content}] zurück. Tiefe Analyse in analysis_md (Markdown). "
                    "Handoff klein halten."
                ),
            ),
            RoleSpec(
                name="integrator",
                model=self._environment_reader.get_str("INTEGRATOR_MODEL", default_model_name),
                reasoning_effort="high",
                system_instructions=(
                    "Du bist INTEGRATOR/VERIFIER. Tools/Commands sind erlaubt. Du darfst Dateien LESEN "
                    "und SCHREIBEN. Prüfe Plan/Änderungen. Gib status DONE|CONTINUE + next_owner zurück. "
                    "Tiefe Analyse in analysis_md (Markdown)."
                ),
            ),
        ]
        return role_specs

    def json_contract_instruction(self) -> str:
        contract_text = (
            "\nFORMAT-VERTRAG (streng):\n"
            "- Antworte mit GENAU EINEM gültigen JSON-Objekt.\n"
            "- KEIN Text außerhalb des JSON. KEIN Markdown-Codefence.\n"
            "- Wenn unklar: gib JSON mit Feld \"error\" zurück.\n"
        )
        return contract_text

    def schema_hint_non_json(self, role_name: str) -> str:
        schema_hint = ""
        if role_name == "planner":
            schema_hint = (
                "\nSCHEMA-HINWEIS (planner, PSEUDO):\n"
                "summary: <string>\n"
                "tasks: [ { id: <string>, title: <string>, owner: architect|implementer|integrator, "
                "priority: <int> } ]\n"
                "next_owner: architect|implementer|integrator\n"
                "notes: <string>\n"
            )
        elif role_name == "implementer":
            schema_hint = (
                "\nSCHEMA-HINWEIS (implementer, PSEUDO):\n"
                "summary: <string>\n"
                "files: [ { path: <string>, content: <string> } ]\n"
                "analysis_md: <markdown>\n"
                "analysis_md_path: <string>  # setzt Controller\n"
                "next_owner_suggestion: planner\n"
            )
        else:
            schema_hint = (
                f"\nSCHEMA-HINWEIS ({role_name}, PSEUDO):\n"
                "summary: <string>\n"
                "key_points: [<string>]\n"
                "requests: { need_more_context: <bool>, files: [<string>], why: <string> }\n"
                "analysis_md: <markdown>\n"
                "analysis_md_path: <string>  # setzt Controller\n"
                "status: <DONE|CONTINUE?>\n"
                "next_owner_suggestion: planner\n"
            )
        return schema_hint


DEFAULT_ROLE_SPEC_CATALOG = RoleSpecCatalog()
ROLE_SPECS = DEFAULT_ROLE_SPEC_CATALOG.build_role_specs()
