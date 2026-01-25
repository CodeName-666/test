"""Specifications for each Codex role (planner / architect / implementer / integrator)."""
import os
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class RoleSpec:
    name: str
    model: str
    reasoning_effort: Optional[str]
    system_instructions: str


DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "gpt-5.1-codex-mini")

ROLE_SPECS: List[RoleSpec] = [
    RoleSpec(
        name="planner",
        model=os.environ.get("PLANNER_MODEL", DEFAULT_MODEL),
        reasoning_effort="high",
        system_instructions=(
            "Du bist PLANNER. Plane und delegiere. Gib next_owner zurück. "
            "Tools/Commands sind erlaubt. Du darfst Dateien NUR LESEN, NICHT schreiben. "
            "Nur JSON, kein Zusatztext."
        ),
    ),
    RoleSpec(
        name="architect",
        model=os.environ.get("ARCHITECT_MODEL", DEFAULT_MODEL),
        reasoning_effort="high",
        system_instructions=(
            "Du bist ARCHITECT. Tools/Commands sind erlaubt. Du darfst Dateien NUR LESEN, "
            "NICHT schreiben. Tiefe Analyse in analysis_md (Markdown String im JSON). "
            "Handoff klein halten."
        ),
    ),
    RoleSpec(
        name="implementer",
        model=os.environ.get("IMPLEMENTER_MODEL", DEFAULT_MODEL),
        reasoning_effort="high",
        system_instructions=(
            "Du bist IMPLEMENTER. Tools/Commands sind erlaubt. Du darfst Dateien NUR LESEN, "
            "NICHT schreiben. Gib Dateiänderungen ausschließlich als Vorschlag im Feld "
            "files=[{path,content}] zurück. Tiefe Analyse in analysis_md (Markdown). Handoff klein halten."
        ),
    ),
    RoleSpec(
        name="integrator",
        model=os.environ.get("INTEGRATOR_MODEL", DEFAULT_MODEL),
        reasoning_effort="high",
        system_instructions=(
            "Du bist INTEGRATOR/VERIFIER. Tools/Commands sind erlaubt. Du darfst Dateien LESEN "
            "und SCHREIBEN. Prüfe Plan/Änderungen. Gib status DONE|CONTINUE + next_owner zurück. "
            "Tiefe Analyse in analysis_md (Markdown)."
        ),
    ),
]


def json_contract_instruction() -> str:
    return (
        "\nFORMAT-VERTRAG (streng):\n"
        "- Antworte mit GENAU EINEM gültigen JSON-Objekt.\n"
        "- KEIN Text außerhalb des JSON. KEIN Markdown-Codefence.\n"
        "- Wenn unklar: gib JSON mit Feld \"error\" zurück.\n"
    )


def schema_hint_non_json(role: str) -> str:
    if role == "planner":
        return (
            "\nSCHEMA-HINWEIS (planner, PSEUDO):\n"
            "summary: <string>\n"
            "tasks: [ { id: <string>, title: <string>, owner: architect|implementer|integrator, priority: <int> } ]\n"
            "next_owner: architect|implementer|integrator\n"
            "notes: <string>\n"
        )
    if role == "implementer":
        return (
            "\nSCHEMA-HINWEIS (implementer, PSEUDO):\n"
            "summary: <string>\n"
            "files: [ { path: <string>, content: <string> } ]\n"
            "analysis_md: <markdown>\n"
            "analysis_md_path: <string>  # setzt Controller\n"
            "next_owner_suggestion: planner\n"
        )
    return (
        f"\nSCHEMA-HINWEIS ({role}, PSEUDO):\n"
        "summary: <string>\n"
        "key_points: [<string>]\n"
        "requests: { need_more_context: <bool>, files: [<string>], why: <string> }\n"
        "analysis_md: <markdown>\n"
        "analysis_md_path: <string>  # setzt Controller\n"
        "status: <DONE|CONTINUE?>\n"
        "next_owner_suggestion: planner\n"
    )
