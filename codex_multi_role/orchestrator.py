"""Core orchestrator that runs multiple Codex roles in cycles."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .codex_role_client import CodexRoleClient
from defaults import DEFAULT_ENVIRONMENT, DEFAULT_JSON_FORMATTER, DEFAULT_LOGGER
from .env_utils import EnvironmentReader
from .json_utils import JsonPayloadFormatter
from .logging import TimestampLogger
from .data.orchestrator_config import OrchestratorConfig
from defaults import (
    DEFAULT_PLANNER_TIMEOUT_S,
    DEFAULT_ROLE_TIMEOUT_S,
    PLANNER_TIMEOUT_ENV,
    PYTEST_CMD_ENV,
    ROLE_TIMEOUT_ENV,
)
from .role_spec import RoleSpec, RoleSpecCatalog
from defaults import DEFAULT_ROLE_SPEC_CATALOG
from .data.turn_result import TurnResult


class CodexRunsOrchestratorV2:
    """Coordinate multiple Codex roles and persist their outputs."""

    def __init__(
        self,
        role_specifications: List[RoleSpec],
        configuration: OrchestratorConfig,
        environment_reader: EnvironmentReader = DEFAULT_ENVIRONMENT,
        json_formatter: JsonPayloadFormatter = DEFAULT_JSON_FORMATTER,
        logger: TimestampLogger = DEFAULT_LOGGER,
        role_spec_catalog: RoleSpecCatalog = DEFAULT_ROLE_SPEC_CATALOG,
    ) -> None:
        self.configuration = configuration
        self._environment_reader = environment_reader
        self._json_formatter = json_formatter
        self._logger = logger
        self._role_spec_catalog = role_spec_catalog

        self.role_sequence = [specification.name for specification in role_specifications]
        self.role_specs_by_name = {
            specification.name: specification for specification in role_specifications
        }

        self.run_id = self._build_run_id()
        self.runs_directory = Path(".runs") / self.run_id
        self._ensure_directory(self.runs_directory)

        self.role_clients: Dict[str, CodexRoleClient] = {}
        for specification in role_specifications:
            client = CodexRoleClient(
                role_name=specification.name,
                model=specification.model,
                reasoning_effort=specification.reasoning_effort,
            )
            role_events = self.runs_directory / specification.name / "events.jsonl"
            self._ensure_directory(role_events.parent)
            client.events_file = role_events
            self.role_clients[specification.name] = client

        # Persisted state helps with debugging and auditing runs after completion.
        self.state: Dict[str, Any] = {
            "goal": configuration.goal,
            "latest_json_by_role": {},
            "history": [],
        }

    def _build_run_id(self) -> str:
        """Create a unique run identifier for the output directory."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        random_suffix = uuid.uuid4().hex[:8]
        run_id_value = f"{timestamp}_{random_suffix}"
        return run_id_value

    def _ensure_directory(self, directory_path: Path) -> None:
        """Ensure directories exist before writing output files."""
        directory_path.mkdir(parents=True, exist_ok=True)
        return None

    def start_all(self) -> None:
        """Start all Codex role clients in sequence."""
        for role_name in self.role_sequence:
            self.role_clients[role_name].start()
        return None

    def stop_all(self) -> None:
        """Stop all role clients, swallowing shutdown errors."""
        for client in self.role_clients.values():
            try:
                client.stop()
            except Exception:
                pass
        return None

    def _write_text(self, relative_path: str, content: str) -> str:
        """Write text content to the run directory and return the path."""
        target_path = self.runs_directory / relative_path
        self._ensure_directory(target_path.parent)
        target_path.write_text(content or "", encoding="utf-8")
        return str(target_path)

    def _build_prompt(self, role_name: str, incoming: Optional[Dict[str, Any]]) -> str:
        """Construct the prompt that is sent to a specific role."""
        specification = self.role_specs_by_name[role_name]
        prompt_text = (
            self._role_spec_catalog.format_general_prompt("role_header", role_name=role_name)
            + f"{specification.system_instructions}\n\n"
            + self._role_spec_catalog.format_general_prompt(
                "goal_section",
                goal=self.configuration.goal,
            )
        )
        if incoming:
            prompt_text += self._role_spec_catalog.format_general_prompt(
                "input_section",
                input=self._json_formatter.normalize_json(incoming),
            )
        prompt_text += self._role_spec_catalog.json_contract_instruction()
        prompt_text += self._role_spec_catalog.schema_hint_non_json(role_name)
        prompt_text += self._role_spec_catalog.format_general_prompt("rules_header")
        prompt_text += self._role_spec_catalog.capability_rules(specification.prompt_flags)
        prompt_text += self._role_spec_catalog.format_general_prompt("analysis_rules")
        return prompt_text

    def _build_repair_prompt(self, issue_description: str) -> str:
        """Build a strict JSON-only repair prompt when parsing fails."""
        repair_prompt = (
            f"{issue_description}\n"
            "Bitte liefere GENAU EIN JSON-Objekt und sonst nichts.\n"
            + self._role_spec_catalog.json_contract_instruction()
        )
        return repair_prompt

    def _persist_turn_artifacts(self, turn_directory: str, turn: TurnResult, prompt: str) -> None:
        """Store raw turn artifacts for later inspection."""
        self._write_text(f"{turn_directory}/assistant_text.txt", turn.assistant_text or "")
        self._write_text(f"{turn_directory}/delta_text.txt", turn.delta_text or "")
        self._write_text(f"{turn_directory}/items_text.md", turn.full_items_text or "")
        self._write_text(f"{turn_directory}/prompt.txt", prompt)
        return None

    def _run_and_parse_json_strict(
        self,
        role_name: str,
        prompt: str,
        timeout_s: float,
    ) -> Tuple[TurnResult, Dict[str, Any]]:
        """Run a role turn and parse JSON with optional repair attempts."""
        last_assistant_text = ""
        last_turn: Optional[TurnResult] = None
        parsed_payload: Optional[Dict[str, Any]] = None
        repair_limit = self.configuration.repair_attempts + 1

        for attempt in range(repair_limit):
            turn = self.role_clients[role_name].run_turn(prompt, timeout_s=timeout_s)
            last_turn = turn
            last_assistant_text = (turn.assistant_text or "").strip()

            turn_directory = f"{role_name}/turn_{turn.request_id}"
            self._persist_turn_artifacts(turn_directory, turn, prompt)

            if not last_assistant_text:
                if attempt < self.configuration.repair_attempts:
                    prompt = self._build_repair_prompt(
                        "Deine letzte Antwort konnte nicht als Assistant-Text erfasst werden."
                    )
                    continue
                raise RuntimeError(
                    f"{role_name}: assistant_text missing after turn completion; "
                    "refusing to parse prompt/full_text."
                )

            try:
                parsed_payload = self._json_formatter.parse_json_object_from_assistant_text(
                    last_assistant_text
                )
                break
            except Exception:
                if attempt < self.configuration.repair_attempts:
                    prompt = self._build_repair_prompt(
                        "Deine letzte Antwort war KEIN gültiges JSON-Objekt."
                    )
                    continue
                raise RuntimeError(
                    f"{role_name}: invalid JSON in assistant_text. "
                    f"First 2000 chars:\n{last_assistant_text[:2000]}"
                )

        if last_turn is None or parsed_payload is None:
            raise RuntimeError(f"{role_name}: failed to get valid JSON")

        return last_turn, parsed_payload

    def _reduce_and_store_payload(
        self,
        role_name: str,
        turn: TurnResult,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Store the parsed payload, keeping analysis in a separate markdown file."""
        turn_directory = f"{role_name}/turn_{turn.request_id}"
        reduced_payload = dict(payload)

        analysis_markdown = reduced_payload.pop("analysis_md", None)
        if isinstance(analysis_markdown, str) and analysis_markdown.strip():
            analysis_path = self._write_text(
                f"{turn_directory}/analysis.md",
                analysis_markdown.strip() + "\n",
            )
            reduced_payload["analysis_md_path"] = analysis_path
        else:
            fallback_path = self.runs_directory / f"{turn_directory}/items_text.md"
            reduced_payload["analysis_md_path"] = str(fallback_path)

        self._write_text(
            f"{turn_directory}/handoff.json",
            self._json_formatter.normalize_json(reduced_payload),
        )
        return reduced_payload

    def _is_safe_relative_path(self, path_value: str) -> bool:
        """Prevent directory traversal or absolute paths for file writes."""
        normalized_path = Path(path_value)
        is_safe = not normalized_path.is_absolute() and ".." not in normalized_path.parts
        return is_safe

    def _apply_implementer_files(self, reduced_payload: Dict[str, Any], turn_directory: str) -> None:
        """Apply implementer file suggestions safely to the workspace."""
        files_value = reduced_payload.get("files")
        applied: List[Dict[str, Any]] = []

        if isinstance(files_value, list):
            for entry in files_value:
                if not isinstance(entry, dict):
                    continue
                path_value = entry.get("path")
                content_value = entry.get("content")
                if not isinstance(path_value, str) or not path_value.strip():
                    continue
                if not isinstance(content_value, str):
                    continue

                cleaned_path = path_value.strip()
                if not self._is_safe_relative_path(cleaned_path):
                    applied.append(
                        {
                            "path": cleaned_path,
                            "status": "SKIPPED",
                            "reason": "unsafe path",
                        }
                    )
                    continue

                target_path = Path(".") / cleaned_path
                self._ensure_directory(target_path.parent)
                target_path.write_text(content_value, encoding="utf-8")

                applied.append(
                    {
                        "path": cleaned_path,
                        "status": "WROTE",
                        "bytes": len(content_value.encode("utf-8")),
                    }
                )

        self._write_text(
            f"{turn_directory}/applied_files.json",
            json.dumps(applied, ensure_ascii=False, indent=2),
        )
        return None

    def _run_tests_if_enabled(self) -> None:
        """Run pytest when configured and store output artifacts."""
        if self.configuration.run_tests:
            command_value = os.environ.get(PYTEST_CMD_ENV, self.configuration.pytest_cmd)
            try:
                command_args = shlex.split(command_value)
                process = subprocess.run(
                    command_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=600,
                )
                output = process.stdout.decode("utf-8", errors="replace")
                self._write_text("tests/pytest_output.txt", output)
                self._write_text("tests/pytest_rc.txt", str(process.returncode))
                self._logger.log(f"[tests] pytest rc={process.returncode}")
            except Exception as exc:
                self._write_text("tests/pytest_error.txt", str(exc))
                self._logger.log(f"[tests] pytest failed to run: {exc}")
        return None

    def _select_timeout(self, role_spec: RoleSpec, planner_timeout: float, role_timeout: float) -> float:
        """Select timeout based on role behavior configuration."""
        timeout_value = role_timeout
        if role_spec.behaviors.timeout_policy == "planner":
            timeout_value = planner_timeout
        return timeout_value

    def _update_state(self, role_name: str, turn: TurnResult, reduced_payload: Dict[str, Any]) -> None:
        """Record the latest payload and history for a role."""
        self.state["latest_json_by_role"][role_name] = reduced_payload
        self.state["history"].append(
            {"role": role_name, "turn": turn.request_id, "handoff": reduced_payload}
        )
        return None

    def _role_signaled_done(self, role_spec: RoleSpec, reduced_payload: Dict[str, Any]) -> bool:
        """Check whether a role signaled completion, if allowed."""
        if not role_spec.behaviors.can_finish:
            return False
        status_value = reduced_payload.get("status")
        is_done = isinstance(status_value, str) and status_value.strip().upper() == "DONE"
        return is_done

    def _persist_controller_state(self) -> None:
        """Persist the orchestrator state at the end of the run."""
        state_path = self.runs_directory / "controller_state.json"
        state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return None

    def run(self) -> None:
        """Run the orchestrator through the configured number of cycles."""
        self.start_all()
        self._logger.log(f"Run folder: {self.runs_directory}")

        incoming_payload: Optional[Dict[str, Any]] = None
        planner_timeout = self._environment_reader.get_float(
            PLANNER_TIMEOUT_ENV,
            DEFAULT_PLANNER_TIMEOUT_S,
        )
        role_timeout = self._environment_reader.get_float(
            ROLE_TIMEOUT_ENV,
            DEFAULT_ROLE_TIMEOUT_S,
        )
        stop_requested = False

        try:
            for cycle_index in range(1, self.configuration.cycles + 1):
                self._logger.log(f"=== Cycle {cycle_index}/{self.configuration.cycles} ===")

                for role_name in self.role_sequence:
                    if stop_requested:
                        break

                    role_spec = self.role_specs_by_name[role_name]
                    prompt = self._build_prompt(role_name, incoming_payload)
                    timeout_value = self._select_timeout(
                        role_spec,
                        planner_timeout,
                        role_timeout,
                    )

                    turn, payload = self._run_and_parse_json_strict(
                        role_name,
                        prompt,
                        timeout_s=timeout_value,
                    )
                    reduced_payload = self._reduce_and_store_payload(role_name, turn, payload)

                    if role_spec.behaviors.apply_files:
                        turn_directory = f"{role_name}/turn_{turn.request_id}"
                        self._apply_implementer_files(reduced_payload, turn_directory)
                        self._run_tests_if_enabled()

                    self._update_state(role_name, turn, reduced_payload)
                    incoming_payload = reduced_payload

                    if self._role_signaled_done(role_spec, reduced_payload):
                        self._logger.log(f"{role_name} indicates DONE. Stopping.")
                        stop_requested = True
        finally:
            self._persist_controller_state()
            self.stop_all()

        return None
