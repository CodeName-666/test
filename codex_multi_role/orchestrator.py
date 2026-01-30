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

PYTEST_TIMEOUT_S = 600.0
ANALYSIS_KEY = "analysis_md"
ANALYSIS_PATH_KEY = "analysis_md_path"
FILES_KEY = "files"

APPLIED_STATUS_WROTE = "WROTE"
APPLIED_STATUS_SKIPPED = "SKIPPED"
APPLIED_REASON_UNSAFE_PATH = "unsafe path"
APPLIED_REASON_INVALID_ENTRY = "invalid entry"
APPLIED_REASON_INVALID_PATH = "invalid path"
APPLIED_REASON_INVALID_CONTENT = "invalid content"
APPLIED_REASON_INVALID_FILES = "files is not a list"


class CodexRunsOrchestratorV2:
    """Coordinate multiple Codex roles and persist their outputs.

    This orchestrator runs each configured role in sequence for a configured number
    of cycles and persists artifacts for auditability.
    """

    def __init__(
        self,
        role_specifications: List[RoleSpec],
        configuration: OrchestratorConfig,
        environment_reader: EnvironmentReader = DEFAULT_ENVIRONMENT,
        json_formatter: JsonPayloadFormatter = DEFAULT_JSON_FORMATTER,
        logger: TimestampLogger = DEFAULT_LOGGER,
        role_spec_catalog: RoleSpecCatalog = DEFAULT_ROLE_SPEC_CATALOG,
    ) -> None:
        """Initialize the orchestrator with role specifications and configuration.

        Args:
            role_specifications: Ordered list of RoleSpec objects.
            configuration: Orchestrator configuration values.
            environment_reader: Reader for environment values.
            json_formatter: JSON formatter for payload serialization.
            logger: Logger for run output.
            role_spec_catalog: Catalog used for prompt formatting and schema hints.

        Raises:
            TypeError: If inputs have invalid types.
            ValueError: If role_specifications is empty or invalid.
        """
        self._validate_init_inputs(
            role_specifications=role_specifications,
            configuration=configuration,
            environment_reader=environment_reader,
            json_formatter=json_formatter,
            logger=logger,
            role_spec_catalog=role_spec_catalog,
        )

        self.configuration = configuration
        self._environment_reader = environment_reader
        self._json_formatter = json_formatter
        self._logger = logger
        self._role_spec_catalog = role_spec_catalog

        self.role_sequence = self._build_role_sequence(role_specifications)
        self.role_specs_by_name = self._build_role_spec_index(role_specifications)

        self.run_id = self._build_run_id()
        self.runs_directory = Path(".runs") / self.run_id
        self._ensure_directory(self.runs_directory)

        self.role_clients = self._build_role_clients(role_specifications)

        # Persisted state helps with debugging and auditing runs after completion.
        self.state = self._build_initial_state(configuration.goal)
        return None

    def _validate_init_inputs(
        self,
        role_specifications: List[RoleSpec],
        configuration: OrchestratorConfig,
        environment_reader: EnvironmentReader,
        json_formatter: JsonPayloadFormatter,
        logger: TimestampLogger,
        role_spec_catalog: RoleSpecCatalog,
    ) -> None:
        """Validate constructor inputs.

        Raises:
            TypeError: If inputs have invalid types.
            ValueError: If role_specifications is empty.
        """
        if isinstance(role_specifications, list):
            if role_specifications:
                self._validate_role_specifications(role_specifications)
            else:
                raise ValueError("role_specifications must not be empty")
        else:
            raise TypeError("role_specifications must be a list")

        if not isinstance(configuration, OrchestratorConfig):
            raise TypeError("configuration must be an OrchestratorConfig")
        if not isinstance(environment_reader, EnvironmentReader):
            raise TypeError("environment_reader must be an EnvironmentReader")
        if not isinstance(json_formatter, JsonPayloadFormatter):
            raise TypeError("json_formatter must be a JsonPayloadFormatter")
        if not isinstance(logger, TimestampLogger):
            raise TypeError("logger must be a TimestampLogger")
        if not isinstance(role_spec_catalog, RoleSpecCatalog):
            raise TypeError("role_spec_catalog must be a RoleSpecCatalog")
        return None

    def _validate_role_specifications(self, role_specifications: List[RoleSpec]) -> None:
        """Validate the role specifications list contents.

        Raises:
            TypeError: If any entry is not a RoleSpec.
            ValueError: If any role name is empty or duplicated.
        """
        role_names: List[str] = []
        for index, specification in enumerate(role_specifications):
            if isinstance(specification, RoleSpec):
                role_names.append(specification.name)
            else:
                raise TypeError(f"role_specifications[{index}] must be a RoleSpec")

        unique_names = set()
        for name in role_names:
            if isinstance(name, str) and name.strip():
                if name in unique_names:
                    raise ValueError(f"duplicate role name: {name}")
                unique_names.add(name)
            else:
                raise ValueError("role_spec name must not be empty")
        return None

    def _build_role_sequence(self, role_specifications: List[RoleSpec]) -> List[str]:
        """Build the role execution sequence from specifications."""
        role_sequence = [specification.name for specification in role_specifications]
        result = role_sequence
        return result

    def _build_role_spec_index(self, role_specifications: List[RoleSpec]) -> Dict[str, RoleSpec]:
        """Index role specifications by name."""
        role_specs_by_name = {
            specification.name: specification for specification in role_specifications
        }
        result = role_specs_by_name
        return result

    def _build_role_clients(self, role_specifications: List[RoleSpec]) -> Dict[str, CodexRoleClient]:
        """Create Codex role clients and configure event log paths."""
        role_clients: Dict[str, CodexRoleClient] = {}
        for specification in role_specifications:
            client = CodexRoleClient(
                role_name=specification.name,
                model=specification.model,
                reasoning_effort=specification.reasoning_effort,
            )
            role_events = self.runs_directory / specification.name / "events.jsonl"
            self._ensure_directory(role_events.parent)
            client.events_file = role_events
            role_clients[specification.name] = client
        result = role_clients
        return result

    def _build_initial_state(self, goal: str) -> Dict[str, Any]:
        """Build the initial orchestrator state structure."""
        state: Dict[str, Any] = {
            "goal": goal,
            "latest_json_by_role": {},
            "history": [],
        }
        result = state
        return result

    def _build_run_id(self) -> str:
        """Create a unique run identifier for the output directory.

        Returns:
            Unique run identifier string.
        """
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        random_suffix = uuid.uuid4().hex[:8]
        run_id_value = f"{timestamp}_{random_suffix}"
        return run_id_value

    def _ensure_directory(self, directory_path: Path) -> None:
        """Ensure directories exist before writing output files.

        Args:
            directory_path: Directory path to create.

        Raises:
            TypeError: If directory_path is not a pathlib.Path.
        """
        if isinstance(directory_path, Path):
            target_path = directory_path
        else:
            raise TypeError("directory_path must be a pathlib.Path")
        target_path.mkdir(parents=True, exist_ok=True)
        return None

    def start_all(self) -> None:
        """Start all Codex role clients in sequence.

        Side Effects:
            Starts subprocesses for all configured roles.
        """
        for role_name in self.role_sequence:
            self.role_clients[role_name].start()
        return None

    def stop_all(self) -> None:
        """Stop all role clients, logging shutdown errors."""
        for client in self.role_clients.values():
            self._stop_client_safely(client)
        return None

    def _stop_client_safely(self, client: CodexRoleClient) -> None:
        """Stop a role client and log errors instead of raising."""
        try:
            client.stop()
        except Exception as exc:
            self._logger.log(f"[warn] failed to stop role client: {exc}")
        return None

    def _write_text(self, relative_path: str, content: str) -> str:
        """Write text content to the run directory and return the path.

        Args:
            relative_path: Relative path under the run directory.
            content: Text content to write.

        Returns:
            Absolute path to the written file as a string.

        Raises:
            TypeError: If relative_path or content has an invalid type.
            ValueError: If relative_path is empty.
        """
        if isinstance(relative_path, str):
            if relative_path.strip():
                normalized_path = relative_path.strip()
            else:
                raise ValueError("relative_path must not be empty")
        else:
            raise TypeError("relative_path must be a string")
        if isinstance(content, str):
            normalized_content = content
        else:
            raise TypeError("content must be a string")

        target_path = self.runs_directory / normalized_path
        self._ensure_directory(target_path.parent)
        target_path.write_text(normalized_content, encoding="utf-8")
        return str(target_path)

    def _build_prompt(self, role_name: str, incoming: Optional[Dict[str, Any]]) -> str:
        """Construct the prompt that is sent to a specific role.

        Args:
            role_name: Role name to build a prompt for.
            incoming: Optional incoming payload from the previous role.

        Returns:
            Rendered prompt string for the role.

        Raises:
            TypeError: If role_name is not a string or incoming is not a dict/None.
            KeyError: If role_name is not configured.
        """
        if isinstance(role_name, str):
            if role_name.strip():
                normalized_role = role_name
            else:
                raise ValueError("role_name must not be empty")
        else:
            raise TypeError("role_name must be a string")
        if incoming is None:
            incoming_payload = None
        elif isinstance(incoming, dict):
            incoming_payload = incoming
        else:
            raise TypeError("incoming must be a dict or None")

        if normalized_role in self.role_specs_by_name:
            specification = self.role_specs_by_name[normalized_role]
        else:
            raise KeyError(f"role not configured: {normalized_role}")
        prompt_text = (
            self._role_spec_catalog.format_general_prompt("role_header", role_name=normalized_role)
            + f"{specification.system_instructions}\n\n"
            + self._role_spec_catalog.format_general_prompt(
                "goal_section",
                goal=self.configuration.goal,
            )
        )
        if incoming_payload:
            prompt_text += self._role_spec_catalog.format_general_prompt(
                "input_section",
                input=self._json_formatter.normalize_json(incoming_payload),
            )
        prompt_text += self._role_spec_catalog.json_contract_instruction()
        prompt_text += self._role_spec_catalog.schema_hint_non_json(normalized_role)
        prompt_text += self._role_spec_catalog.format_general_prompt("rules_header")
        prompt_text += self._role_spec_catalog.capability_rules(specification.prompt_flags)
        prompt_text += self._role_spec_catalog.format_general_prompt("analysis_rules")
        return prompt_text

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
        return repair_prompt

    def _persist_turn_artifacts(self, turn_directory: str, turn: TurnResult, prompt: str) -> None:
        """Store raw turn artifacts for later inspection.

        Args:
            turn_directory: Relative turn directory path.
            turn: TurnResult containing assistant outputs.
            prompt: Prompt text sent to the role.

        Raises:
            TypeError: If inputs have invalid types.
            ValueError: If turn_directory is empty.
        """
        if isinstance(turn_directory, str):
            if turn_directory.strip():
                directory = turn_directory.strip()
            else:
                raise ValueError("turn_directory must not be empty")
        else:
            raise TypeError("turn_directory must be a string")
        if not isinstance(turn, TurnResult):
            raise TypeError("turn must be a TurnResult")
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")

        self._write_text(f"{directory}/assistant_text.txt", turn.assistant_text or "")
        self._write_text(f"{directory}/delta_text.txt", turn.delta_text or "")
        self._write_text(f"{directory}/items_text.md", turn.full_items_text or "")
        self._write_text(f"{directory}/prompt.txt", prompt)
        return None

    def _run_and_parse_json_strict(
        self,
        role_name: str,
        prompt: str,
        timeout_s: float,
    ) -> Tuple[TurnResult, Dict[str, Any]]:
        """Run a role turn and parse JSON with optional repair attempts.

        Args:
            role_name: Role name to execute.
            prompt: Prompt text for the role.
            timeout_s: Timeout in seconds for the role turn.

        Returns:
            Tuple of (TurnResult, parsed JSON payload).

        Raises:
            TypeError: If inputs have invalid types.
            ValueError: If role_name is empty or timeout_s is invalid.
            RuntimeError: If JSON parsing fails after repair attempts.
        """
        if isinstance(role_name, str):
            if role_name.strip():
                normalized_role = role_name
            else:
                raise ValueError("role_name must not be empty")
        else:
            raise TypeError("role_name must be a string")
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        if isinstance(timeout_s, (int, float)):
            if timeout_s > 0:
                timeout_value = float(timeout_s)
            else:
                raise ValueError("timeout_s must be greater than zero")
        else:
            raise TypeError("timeout_s must be a number")

        last_assistant_text = ""
        last_turn: Optional[TurnResult] = None
        parsed_payload: Optional[Dict[str, Any]] = None
        repair_limit = self.configuration.repair_attempts + 1

        for attempt in range(repair_limit):
            turn = self.role_clients[normalized_role].run_turn(prompt, timeout_s=timeout_value)
            last_turn = turn
            last_assistant_text = (turn.assistant_text or "").strip()

            turn_directory = f"{normalized_role}/turn_{turn.request_id}"
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
                    f"{normalized_role}: invalid JSON in assistant_text. "
                    f"First 2000 chars:\n{last_assistant_text[:2000]}"
                )

        if last_turn is None or parsed_payload is None:
            raise RuntimeError(f"{normalized_role}: failed to get valid JSON")

        return last_turn, parsed_payload

    def _reduce_and_store_payload(
        self,
        role_name: str,
        turn: TurnResult,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Store the parsed payload, keeping analysis in a separate markdown file.

        Args:
            role_name: Role name for directory placement.
            turn: TurnResult containing assistant output.
            payload: Parsed JSON payload from the role.

        Returns:
            Reduced payload with analysis_md_path injected.

        Raises:
            TypeError: If inputs have invalid types.
            ValueError: If role_name is empty.
        """
        if isinstance(role_name, str):
            if role_name.strip():
                normalized_role = role_name
            else:
                raise ValueError("role_name must not be empty")
        else:
            raise TypeError("role_name must be a string")
        if not isinstance(turn, TurnResult):
            raise TypeError("turn must be a TurnResult")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")

        turn_directory = f"{normalized_role}/turn_{turn.request_id}"
        reduced_payload = dict(payload)

        analysis_markdown = reduced_payload.pop(ANALYSIS_KEY, None)
        if analysis_markdown is None:
            fallback_path = self.runs_directory / f"{turn_directory}/items_text.md"
            reduced_payload[ANALYSIS_PATH_KEY] = str(fallback_path)
        elif isinstance(analysis_markdown, str):
            if analysis_markdown.strip():
                analysis_path = self._write_text(
                    f"{turn_directory}/analysis.md",
                    analysis_markdown.strip() + "\n",
                )
                reduced_payload[ANALYSIS_PATH_KEY] = analysis_path
            else:
                fallback_path = self.runs_directory / f"{turn_directory}/items_text.md"
                reduced_payload[ANALYSIS_PATH_KEY] = str(fallback_path)
        else:
            raise TypeError(f"{ANALYSIS_KEY} must be a string or null")

        self._write_text(
            f"{turn_directory}/handoff.json",
            self._json_formatter.normalize_json(reduced_payload),
        )
        return reduced_payload

    def _is_safe_relative_path(self, path_value: str) -> bool:
        """Prevent directory traversal or absolute paths for file writes.

        Args:
            path_value: Relative path candidate.

        Returns:
            True if the path is safe and relative, otherwise False.

        Raises:
            TypeError: If path_value is not a string.
            ValueError: If path_value is empty.
        """
        if isinstance(path_value, str):
            if path_value.strip():
                normalized_value = path_value.strip()
            else:
                raise ValueError("path_value must not be empty")
        else:
            raise TypeError("path_value must be a string")
        normalized_path = Path(normalized_value)
        is_safe = not normalized_path.is_absolute() and ".." not in normalized_path.parts
        return is_safe

    def _apply_implementer_files(self, reduced_payload: Dict[str, Any], turn_directory: str) -> None:
        """Apply implementer file suggestions safely to the workspace.

        Args:
            reduced_payload: Payload containing optional file change suggestions.
            turn_directory: Relative turn directory path for artifacts.

        Raises:
            TypeError: If inputs have invalid types.
            ValueError: If turn_directory is empty.
        """
        if isinstance(reduced_payload, dict):
            payload = reduced_payload
        else:
            raise TypeError("reduced_payload must be a dict")
        if isinstance(turn_directory, str):
            if turn_directory.strip():
                directory = turn_directory.strip()
            else:
                raise ValueError("turn_directory must not be empty")
        else:
            raise TypeError("turn_directory must be a string")

        files_value = payload.get(FILES_KEY)
        applied: List[Dict[str, Any]] = []

        if files_value is None:
            applied = []
        elif isinstance(files_value, list):
            for entry in files_value:
                applied.append(self._process_file_entry(entry))
        else:
            applied.append({"status": APPLIED_STATUS_SKIPPED, "reason": APPLIED_REASON_INVALID_FILES})

        self._write_text(
            f"{directory}/applied_files.json",
            json.dumps(applied, ensure_ascii=False, indent=2),
        )
        return None

    def _process_file_entry(self, entry: Any) -> Dict[str, Any]:
        """Validate and apply a single file entry.

        Args:
            entry: File entry dictionary with path/content fields.

        Returns:
            Applied entry result dictionary.
        """
        result: Dict[str, Any] = {}
        if isinstance(entry, dict):
            payload = entry
            path_value = payload.get("path")
            content_value = payload.get("content")
            if not isinstance(path_value, str) or not path_value.strip():
                result = {
                    "status": APPLIED_STATUS_SKIPPED,
                    "reason": APPLIED_REASON_INVALID_PATH,
                }
            elif not isinstance(content_value, str):
                result = {
                    "path": path_value.strip(),
                    "status": APPLIED_STATUS_SKIPPED,
                    "reason": APPLIED_REASON_INVALID_CONTENT,
                }
            else:
                cleaned_path = path_value.strip()
                if not self._is_safe_relative_path(cleaned_path):
                    result = {
                        "path": cleaned_path,
                        "status": APPLIED_STATUS_SKIPPED,
                        "reason": APPLIED_REASON_UNSAFE_PATH,
                    }
                else:
                    target_path = Path(".") / cleaned_path
                    self._ensure_directory(target_path.parent)
                    try:
                        target_path.write_text(content_value, encoding="utf-8")
                        result = {
                            "path": cleaned_path,
                            "status": APPLIED_STATUS_WROTE,
                            "bytes": len(content_value.encode("utf-8")),
                        }
                    except Exception as exc:
                        result = {
                            "path": cleaned_path,
                            "status": APPLIED_STATUS_SKIPPED,
                            "reason": f"write failed: {exc}",
                        }
        else:
            result = {
                "status": APPLIED_STATUS_SKIPPED,
                "reason": APPLIED_REASON_INVALID_ENTRY,
            }
        return result

    def _run_tests_if_enabled(self) -> None:
        """Run pytest when configured and store output artifacts.

        Side Effects:
            Executes pytest and writes output artifacts under the run directory.
        """
        if self.configuration.run_tests:
            command_value = os.environ.get(PYTEST_CMD_ENV, self.configuration.pytest_cmd)
            if isinstance(command_value, str):
                if command_value.strip():
                    normalized_command = command_value
                else:
                    raise ValueError("pytest command must not be empty")
            else:
                raise TypeError("pytest command must be a string")
            try:
                command_args = shlex.split(normalized_command)
                process = subprocess.run(
                    command_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=PYTEST_TIMEOUT_S,
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
        """Select timeout based on role behavior configuration.

        Args:
            role_spec: Role specification for the current role.
            planner_timeout: Planner timeout in seconds.
            role_timeout: Default role timeout in seconds.

        Returns:
            Timeout value to use for the role.

        Raises:
            TypeError: If inputs have invalid types.
            ValueError: If timeout values are not positive.
        """
        if not isinstance(role_spec, RoleSpec):
            raise TypeError("role_spec must be a RoleSpec")
        if isinstance(planner_timeout, (int, float)):
            if planner_timeout > 0:
                planner_value = float(planner_timeout)
            else:
                raise ValueError("planner_timeout must be greater than zero")
        else:
            raise TypeError("planner_timeout must be a number")
        if isinstance(role_timeout, (int, float)):
            if role_timeout > 0:
                role_value = float(role_timeout)
            else:
                raise ValueError("role_timeout must be greater than zero")
        else:
            raise TypeError("role_timeout must be a number")

        timeout_value = role_value
        if role_spec.behaviors.timeout_policy == "planner":
            timeout_value = planner_value
        return timeout_value

    def _update_state(self, role_name: str, turn: TurnResult, reduced_payload: Dict[str, Any]) -> None:
        """Record the latest payload and history for a role.

        Args:
            role_name: Role name to update.
            turn: TurnResult for the role.
            reduced_payload: Reduced JSON payload for the role.

        Raises:
            TypeError: If inputs have invalid types.
            ValueError: If role_name is empty.
        """
        if isinstance(role_name, str):
            if role_name.strip():
                normalized_role = role_name
            else:
                raise ValueError("role_name must not be empty")
        else:
            raise TypeError("role_name must be a string")
        if not isinstance(turn, TurnResult):
            raise TypeError("turn must be a TurnResult")
        if not isinstance(reduced_payload, dict):
            raise TypeError("reduced_payload must be a dict")

        self.state["latest_json_by_role"][normalized_role] = reduced_payload
        self.state["history"].append(
            {"role": normalized_role, "turn": turn.request_id, "handoff": reduced_payload}
        )
        return None

    def _role_signaled_done(self, role_spec: RoleSpec, reduced_payload: Dict[str, Any]) -> bool:
        """Check whether a role signaled completion, if allowed.

        Args:
            role_spec: Role specification for the role.
            reduced_payload: Reduced payload returned by the role.

        Returns:
            True if the role is allowed to finish and signaled DONE.

        Raises:
            TypeError: If inputs have invalid types.
        """
        if not isinstance(role_spec, RoleSpec):
            raise TypeError("role_spec must be a RoleSpec")
        if not isinstance(reduced_payload, dict):
            raise TypeError("reduced_payload must be a dict")

        status_value = reduced_payload.get("status")
        is_done_signal = isinstance(status_value, str) and status_value.strip().upper() == "DONE"
        is_done = role_spec.behaviors.can_finish and is_done_signal
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
        """Run the orchestrator through the configured number of cycles.

        Side Effects:
            Starts role clients, runs them in sequence, and writes artifacts.
        """
        self.start_all()
        self._logger.log(f"Run folder: {self.runs_directory}")

        incoming_payload: Optional[Dict[str, Any]] = None
        planner_timeout, role_timeout = self._resolve_timeouts()
        stop_requested = False

        try:
            for cycle_index in range(1, self.configuration.cycles + 1):
                incoming_payload, stop_requested = self._run_cycle(
                    cycle_index=cycle_index,
                    incoming_payload=incoming_payload,
                    planner_timeout=planner_timeout,
                    role_timeout=role_timeout,
                    stop_requested=stop_requested,
                )
                if stop_requested:
                    break
        finally:
            self._persist_controller_state()
            self.stop_all()

        return None

    def _resolve_timeouts(self) -> Tuple[float, float]:
        """Resolve planner and role timeouts from environment/config.

        Returns:
            Tuple of (planner_timeout, role_timeout) in seconds.
        """
        planner_timeout = self._environment_reader.get_float(
            PLANNER_TIMEOUT_ENV,
            DEFAULT_PLANNER_TIMEOUT_S,
        )
        role_timeout = self._environment_reader.get_float(
            ROLE_TIMEOUT_ENV,
            DEFAULT_ROLE_TIMEOUT_S,
        )
        result = (planner_timeout, role_timeout)
        return result

    def _run_cycle(
        self,
        cycle_index: int,
        incoming_payload: Optional[Dict[str, Any]],
        planner_timeout: float,
        role_timeout: float,
        stop_requested: bool,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Run a single orchestrator cycle across all roles.

        Args:
            cycle_index: Current cycle index.
            incoming_payload: Payload from the previous role.
            planner_timeout: Planner timeout in seconds.
            role_timeout: Default role timeout in seconds.
            stop_requested: Whether a stop has already been requested.

        Returns:
            Tuple of (updated incoming_payload, stop_requested flag).
        """
        self._logger.log(f"=== Cycle {cycle_index}/{self.configuration.cycles} ===")
        current_payload = incoming_payload
        stop_flag = stop_requested

        for role_name in self.role_sequence:
            if stop_flag:
                break
            current_payload, stop_flag = self._run_role_turn(
                role_name=role_name,
                incoming_payload=current_payload,
                planner_timeout=planner_timeout,
                role_timeout=role_timeout,
            )
        result = (current_payload, stop_flag)
        return result

    def _run_role_turn(
        self,
        role_name: str,
        incoming_payload: Optional[Dict[str, Any]],
        planner_timeout: float,
        role_timeout: float,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Run a single role turn and update orchestrator state.

        Args:
            role_name: Role name to run.
            incoming_payload: Payload from the previous role.
            planner_timeout: Planner timeout in seconds.
            role_timeout: Default role timeout in seconds.

        Returns:
            Tuple of (updated incoming_payload, stop_requested flag).
        """
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
        updated_payload: Optional[Dict[str, Any]] = reduced_payload

        stop_requested = False
        if self._role_signaled_done(role_spec, reduced_payload):
            self._logger.log(f"{role_name} indicates DONE. Stopping.")
            stop_requested = True

        result = (updated_payload, stop_requested)
        return result
