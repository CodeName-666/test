"""Client that interacts with a Codex app-server role process."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from defaults import (
    DEFAULT_ALLOW_COMMANDS,
    DEFAULT_AUTO_APPROVE_COMMANDS,
    DEFAULT_AUTO_APPROVE_FILE_CHANGES,
    DEFAULT_ENVIRONMENT,
    DEFAULT_EVENT_PARSER,
    DEFAULT_HARD_TIMEOUT_S,
    DEFAULT_LOGGER,
    DEFAULT_SYSTEM_LOCATOR,
    ENV_ALLOW_COMMANDS,
    ENV_AUTO_APPROVE_COMMANDS,
    ENV_AUTO_APPROVE_FILE_CHANGES,
    ENV_HARD_TIMEOUT_S,
    FULL_ACCESS,
)
from .env_utils import EnvironmentReader
from .event_utils import EventParser
from .logging import TimestampLogger
from .system_utils import SystemLocator
from .data.turn_result import TurnResult

INIT_REQUEST_ID = 0
THREAD_START_REQUEST_ID = 1
THREAD_ID_TIMEOUT_S = 15.0
EVENT_QUEUE_TIMEOUT_S = 0.2
HARD_TIMEOUT_MULTIPLIER = 3.0
HARD_TIMEOUT_GRACE_S = 300.0

METHOD_ITEM_DELTA = "item/delta"
METHOD_ITEM_COMPLETED = "item/completed"
METHOD_TURN_COMPLETED = "turn/completed"
METHOD_INITIALIZE = "initialize"
METHOD_INITIALIZED = "initialized"
METHOD_THREAD_START = "thread/start"
METHOD_TURN_START = "turn/start"
REQUEST_APPROVAL_SUFFIX = "/requestApproval"
METHOD_COMMAND_APPROVAL = "item/commandExecution/requestApproval"
METHOD_FILE_CHANGE_APPROVAL = "item/fileChange/requestApproval"

CLIENT_VERSION = "0.1.0"

IGNORED_TIMEOUT_METHODS = {
    "thread/tokenUsage/updated",
    "account/rateLimits/updated",
    "codex/event/token_count",
}


@dataclass
class CodexRoleClient:
    """Client that manages a Codex role process and turn lifecycle.

    Attributes:
        role_name: Role name used to label events and logs.
        model: Model name requested from the Codex CLI.
        reasoning_effort: Optional reasoning effort label for the model.
        auto_approve_file_changes: Whether to auto-approve file change requests.
        allow_commands: Whether command execution requests are allowed.
        auto_approve_commands: Whether to auto-approve command requests.
        environment_reader: Environment reader for configuration.
        event_parser: Parser for event stream messages.
        logger: Logger instance for trace output.
        system_locator: System locator for resolving CLI binaries.
        process: Active subprocess handle if started.
        event_queue: Queue of parsed event messages from the process.
        thread_id: Active thread identifier for the role session.
        events_file: Optional JSONL file path for event logging.

    Raises:
        TypeError: If constructed with invalid field types.
    """
    role_name: str
    model: str
    reasoning_effort: Optional[str] = None
    auto_approve_file_changes: bool = field(
        default_factory=lambda: DEFAULT_ENVIRONMENT.get_flag(
            ENV_AUTO_APPROVE_FILE_CHANGES,
            DEFAULT_AUTO_APPROVE_FILE_CHANGES,
        )
    )
    allow_commands: bool = field(
        default_factory=lambda: DEFAULT_ENVIRONMENT.get_flag(
            ENV_ALLOW_COMMANDS,
            DEFAULT_ALLOW_COMMANDS,
        )
    )
    auto_approve_commands: bool = field(
        default_factory=lambda: DEFAULT_ENVIRONMENT.get_flag(
            ENV_AUTO_APPROVE_COMMANDS,
            DEFAULT_AUTO_APPROVE_COMMANDS,
        )
    )
    environment_reader: EnvironmentReader = field(default_factory=lambda: DEFAULT_ENVIRONMENT)
    event_parser: EventParser = field(default_factory=lambda: DEFAULT_EVENT_PARSER)
    logger: TimestampLogger = field(default_factory=lambda: DEFAULT_LOGGER)
    system_locator: SystemLocator = field(default_factory=lambda: DEFAULT_SYSTEM_LOCATOR)

    process: Optional[subprocess.Popen] = None
    event_queue: "queue.Queue[Dict[str, Any]]" = field(default_factory=queue.Queue)
    thread_id: Optional[str] = None
    _request_id_counter: int = 100
    events_file: Optional[Path] = None
    _delta_text_parts: List[str] = field(default_factory=list)
    _completed_item_text_parts: List[str] = field(default_factory=list)
    _assistant_text: Optional[str] = None
    _events_count: int = 0

    def __post_init__(self) -> None:
        """Validate core dataclass fields after initialization.

        Raises:
            TypeError: If any field has an invalid type.
            ValueError: If required string fields are empty.
        """
        self._validate_str_field(self.role_name, "role_name")
        self._validate_str_field(self.model, "model")
        self._validate_optional_str_field(self.reasoning_effort, "reasoning_effort")
        self._validate_bool_field(self.auto_approve_file_changes, "auto_approve_file_changes")
        self._validate_bool_field(self.allow_commands, "allow_commands")
        self._validate_bool_field(self.auto_approve_commands, "auto_approve_commands")
        self._validate_instance_field(self.environment_reader, EnvironmentReader, "environment_reader")
        self._validate_instance_field(self.event_parser, EventParser, "event_parser")
        self._validate_instance_field(self.logger, TimestampLogger, "logger")
        self._validate_instance_field(self.system_locator, SystemLocator, "system_locator")
        self._validate_optional_instance_field(self.events_file, Path, "events_file")
        self._validate_queue_field(self.event_queue, "event_queue")
        return None

    def _validate_str_field(self, value: str, field_name: str) -> None:
        """Validate that a string field is non-empty."""
        if isinstance(value, str):
            if value.strip():
                validated_value = value
            else:
                raise ValueError(f"{field_name} must not be empty")
        else:
            raise TypeError(f"{field_name} must be a string")
        return None

    def _validate_optional_str_field(self, value: Optional[str], field_name: str) -> None:
        """Validate that an optional string field is either None or non-empty."""
        if value is None:
            validated_value = value
        elif isinstance(value, str):
            if value.strip():
                validated_value = value
            else:
                raise ValueError(f"{field_name} must not be empty when provided")
        else:
            raise TypeError(f"{field_name} must be a string or None")
        return None

    def _validate_bool_field(self, value: bool, field_name: str) -> None:
        """Validate that a boolean field has the correct type."""
        if isinstance(value, bool):
            validated_value = value
        else:
            raise TypeError(f"{field_name} must be a boolean")
        return None

    def _validate_instance_field(self, value: Any, expected_type: type, field_name: str) -> None:
        """Validate that a field is an instance of the expected type."""
        if isinstance(value, expected_type):
            validated_value = value
        else:
            raise TypeError(f"{field_name} must be a {expected_type.__name__} instance")
        return None

    def _validate_optional_instance_field(
        self, value: Optional[Any], expected_type: type, field_name: str
    ) -> None:
        """Validate that an optional field is None or an instance of expected type."""
        if value is None:
            validated_value = value
        elif isinstance(value, expected_type):
            validated_value = value
        else:
            raise TypeError(f"{field_name} must be a {expected_type.__name__} or None")
        return None

    def _validate_queue_field(self, value: Any, field_name: str) -> None:
        """Validate that a field is a queue.Queue instance."""
        if isinstance(value, queue.Queue):
            validated_value = value
        else:
            raise TypeError(f"{field_name} must be a queue.Queue instance")
        return None

    def start(self) -> None:
        """Start the Codex app-server process and initialize the role thread.

        Side Effects:
            Spawns the codex app-server process and starts a reader thread.

        Raises:
            RuntimeError: If codex CLI is not found in PATH.
            RuntimeError: If thread id cannot be obtained.
        """
        should_start = self.process is None
        if should_start:
            codex_binary = self.system_locator.find_codex()
            if not codex_binary:
                raise RuntimeError("codex CLI not found in PATH")

            command_line = self._build_command_line(codex_binary)

            # Spawn the app-server process with pipes for bidirectional JSON messages.
            self.process = subprocess.Popen(
                command_line,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )

            self._start_reader_thread()
            self._send_initialize_messages()

            self._send_thread_start()
            self.thread_id = self._wait_for_thread_id(timeout_s=THREAD_ID_TIMEOUT_S)
            self.logger.log(
                f"{self.role_name}: started (thread_id={self.thread_id}, model={self.model})"
            )
        return None

    def stop(self) -> None:
        """Terminate the running process if it exists.

        Side Effects:
            Terminates the subprocess and clears internal state.
        """
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
        self.process = None
        return None

    def _build_command_line(self, codex_binary: str) -> List[str]:
        """Build the command line used to start the Codex app-server.

        Args:
            codex_binary: Absolute path to the codex CLI binary.

        Returns:
            Command line argument list for subprocess.Popen.

        Raises:
            TypeError: If codex_binary is not a string.
            ValueError: If codex_binary is empty.
        """
        if isinstance(codex_binary, str):
            if codex_binary.strip():
                resolved_binary = codex_binary
            else:
                raise ValueError("codex_binary must not be empty")
        else:
            raise TypeError("codex_binary must be a string")

        command_line: List[str] = [resolved_binary]
        if self.reasoning_effort:
            command_line += ["-c", f"model_reasoning_effort={json.dumps(self.reasoning_effort)}"]
        command_line.append("app-server")
        return command_line

    def _start_reader_thread(self) -> None:
        """Begin the background reader thread for server events.

        Side Effects:
            Starts a daemon thread that reads and enqueues event messages.
        """
        reader_thread = threading.Thread(target=self._reader_thread_main, daemon=True)
        reader_thread.start()
        return None

    def _reader_thread_main(self) -> None:
        """Read server events from stdout and feed them into the queue.

        Raises:
            RuntimeError: If process stdout is unavailable.
        """
        process = self.process
        if process is None or process.stdout is None:
            raise RuntimeError("process stdout unavailable; cannot read events")

        for raw_line in iter(process.stdout.readline, b""):
            decoded_line = raw_line.decode("utf-8", errors="replace")
            parsed_message = self.event_parser.parse_event_json_line(decoded_line)
            if parsed_message is None:
                continue
            self._append_event_to_file(parsed_message)
            self.event_queue.put(parsed_message)
        return None

    def _append_event_to_file(self, message: Dict[str, Any]) -> None:
        """Persist events to a JSONL file for debugging and auditability.

        Args:
            message: Parsed event message to write.
        """
        if self.events_file:
            try:
                with self.events_file.open("a", encoding="utf-8") as event_stream:
                    event_stream.write(json.dumps(message, ensure_ascii=False) + "\n")
            except Exception:
                pass
        return None

    def _send_initialize_messages(self) -> None:
        """Send the initialization handshake messages."""
        self._send(
            {
                "method": METHOD_INITIALIZE,
                "id": INIT_REQUEST_ID,
                "params": {
                    "clientInfo": {
                        "name": self.role_name,
                        "title": self.role_name,
                        "version": CLIENT_VERSION,
                    }
                },
            }
        )
        self._send({"method": METHOD_INITIALIZED, "params": {}})
        return None

    def _send_thread_start(self) -> None:
        """Request a new thread from the server for this role."""
        self._send(
            {
                "method": METHOD_THREAD_START,
                "id": THREAD_START_REQUEST_ID,
                "params": {"model": self.model},
            }
        )
        return None

    def _send(self, message: Dict[str, Any]) -> None:
        """Send a JSON message to the server.

        Args:
            message: JSON-serializable message dictionary.

        Raises:
            TypeError: If message is not a dictionary.
            RuntimeError: If process stdin is unavailable.
        """
        if isinstance(message, dict):
            payload_message = message
        else:
            raise TypeError("message must be a dict")
        process = self.process
        if process is None or process.stdin is None:
            raise RuntimeError("process stdin unavailable; cannot send message")
        payload = (json.dumps(payload_message, ensure_ascii=False) + "\n").encode("utf-8")
        process.stdin.write(payload)
        process.stdin.flush()
        return None

    def _wait_for_thread_id(self, timeout_s: float) -> str:
        """Wait for the thread id response from the server.

        Args:
            timeout_s: Maximum time in seconds to wait.

        Returns:
            Thread identifier string.

        Raises:
            TimeoutError: If the thread id is not received in time.
            TypeError: If timeout_s is not a number.
            ValueError: If timeout_s is not positive.
        """
        if isinstance(timeout_s, (int, float)):
            if timeout_s > 0:
                timeout_value = float(timeout_s)
            else:
                raise ValueError("timeout_s must be greater than zero")
        else:
            raise TypeError("timeout_s must be a number")
        deadline = time.time() + timeout_value
        thread_identifier = ""
        while time.time() < deadline and not thread_identifier:
            try:
                message = self.event_queue.get(timeout=EVENT_QUEUE_TIMEOUT_S)
            except queue.Empty:
                continue
            if message.get("id") == THREAD_START_REQUEST_ID:
                result = message.get("result") or {}
                thread = result.get("thread") or {}
                candidate_id = thread.get("id")
                if candidate_id:
                    thread_identifier = candidate_id
        if thread_identifier:
            result = thread_identifier
        else:
            raise TimeoutError(f"{self.role_name}: timed out waiting for thread id")
        return result

    def _reset_turn_buffers(self) -> None:
        """Clear state collected for the current turn."""
        self._delta_text_parts = []
        self._completed_item_text_parts = []
        self._assistant_text = None
        self._events_count = 0
        return None

    def _handle_event(self, message: Dict[str, Any]) -> None:
        """Process event messages to extract assistant text.

        Args:
            message: Event message payload.

        Raises:
            TypeError: If message is not a dictionary.
        """
        if isinstance(message, dict):
            payload = message
        else:
            raise TypeError("message must be a dict")
        method_name = payload.get("method") or ""
        if method_name == METHOD_ITEM_DELTA:
            # Deltas arrive as partial streaming text.
            params = payload.get("params") or {}
            delta = params.get("delta") or params.get("item") or {}
            if isinstance(delta, dict):
                extracted_text = self.event_parser.extract_text_from_item(delta)
                if extracted_text:
                    self._delta_text_parts.append(extracted_text)
        elif method_name == METHOD_ITEM_COMPLETED:
            # Completed items may include the final assistant text.
            item = (payload.get("params") or {}).get("item") or {}
            if isinstance(item, dict):
                item_type_name = self.event_parser.normalize_item_type_name(item.get("type"))
                extracted_text = self.event_parser.extract_text_from_item(item)
                raw_type = item.get("type") or "unknown"
                if extracted_text:
                    self._completed_item_text_parts.append(f"[{raw_type}] {extracted_text}")
                if item_type_name in ("agentmessage", "assistantmessage") and extracted_text:
                    self._assistant_text = extracted_text
        return None

    def _send_approval_response(self, request_id: int, approved: bool) -> None:
        """Send approval responses for requests that require user consent.

        Args:
            request_id: Request identifier from the server.
            approved: Whether to approve the request.

        Raises:
            TypeError: If request_id or approved has an invalid type.
        """
        if isinstance(request_id, int):
            validated_request_id = request_id
        else:
            raise TypeError("request_id must be an integer")
        if isinstance(approved, bool):
            validated_approved = approved
        else:
            raise TypeError("approved must be a boolean")
        self._send({"id": validated_request_id, "result": {"approved": validated_approved}})
        return None

    def _handle_request_approval(self, message: Dict[str, Any]) -> bool:
        """Approve or reject tool/file requests based on configuration.

        Args:
            message: Event message payload.

        Returns:
            True if the message required approval handling, otherwise False.

        Raises:
            TypeError: If message is not a dictionary.
            RuntimeError: If approval is required but cannot be granted.
            ValueError: If request_id is missing for an approval request.
        """
        if isinstance(message, dict):
            payload = message
        else:
            raise TypeError("message must be a dict")

        method_name = (payload.get("method") or "").strip()
        handled = False

        if method_name.endswith(REQUEST_APPROVAL_SUFFIX):
            handled = True
            request_id = payload.get("id")
            if request_id is None:
                raise ValueError("approval request missing id")

            if FULL_ACCESS:
                self._send_approval_response(request_id, True)
                self.logger.log(
                    f"[{self.role_name}] auto-approved approval request "
                    f"(id={request_id}, method={method_name})"
                )
            elif method_name == METHOD_COMMAND_APPROVAL:
                if not self.allow_commands:
                    self._send_approval_response(request_id, False)
                    self.logger.log(
                        f"[{self.role_name}] denied command execution (CODEX_ALLOW_COMMANDS=0)"
                    )
                elif self.auto_approve_commands:
                    self._send_approval_response(request_id, True)
                    self.logger.log(
                        f"[{self.role_name}] auto-approved command execution (id={request_id})"
                    )
                else:
                    raise RuntimeError(
                        f"{self.role_name}: approval required for {method_name}. "
                        "Set FULL_ACCESS=True, or CODEX_AUTO_APPROVE_COMMANDS=1, or disable via "
                        "CODEX_ALLOW_COMMANDS=0."
                    )
            elif method_name == METHOD_FILE_CHANGE_APPROVAL:
                if self.auto_approve_file_changes:
                    self._send_approval_response(request_id, True)
                    self.logger.log(
                        f"[{self.role_name}] auto-approved file change request (id={request_id})"
                    )
                else:
                    raise RuntimeError(
                        f"{self.role_name}: approval required for {method_name}. "
                        "Set FULL_ACCESS=True or CODEX_AUTO_APPROVE_FILE_CHANGES=1."
                    )
            else:
                raise RuntimeError(
                    f"{self.role_name}: approval required for {method_name}. "
                    "Set FULL_ACCESS=True or CODEX_AUTO_APPROVE_FILE_CHANGES=1."
                )
        return handled

    def _log_event(self, method_name: str) -> None:
        """Log server events that are helpful for tracing progress.

        Args:
            method_name: Event method name to log.

        Raises:
            TypeError: If method_name is not a string.
        """
        if isinstance(method_name, str):
            event_name = method_name
        else:
            raise TypeError("method_name must be a string")
        self.logger.log(f"[{self.role_name}] event: {event_name}")
        return None

    def run_turn(self, prompt: str, timeout_s: float = 180.0) -> TurnResult:
        """Run a single Codex turn and return the aggregated results.

        Args:
            prompt: Prompt text sent to the role.
            timeout_s: Idle timeout in seconds for turn completion.

        Returns:
            TurnResult containing aggregated assistant output.

        Raises:
            TypeError: If prompt/timeout_s have invalid types.
            ValueError: If timeout_s is not positive.
            RuntimeError: If thread_id is unavailable or the turn fails to complete.
            TimeoutError: If idle or hard timeouts are exceeded.
        """
        self._validate_prompt(prompt)
        timeout_value = self._validate_timeout(timeout_s)
        self.start()
        self._ensure_thread_id()

        request_id = self._next_request_id()
        self._reset_turn_buffers()
        self._send_turn_start(request_id, prompt)

        deadline, hard_deadline = self._build_deadlines(timeout_value)
        turn_result = self._wait_for_turn_completion(
            request_id=request_id,
            timeout_s=timeout_value,
            deadline=deadline,
            hard_deadline=hard_deadline,
        )
        return turn_result

    def _validate_prompt(self, prompt: str) -> None:
        """Validate the prompt input.

        Args:
            prompt: Prompt text.

        Raises:
            TypeError: If prompt is not a string.
            ValueError: If prompt is empty.
        """
        if isinstance(prompt, str):
            if prompt.strip():
                validated_prompt = prompt
            else:
                raise ValueError("prompt must not be empty")
        else:
            raise TypeError("prompt must be a string")
        return None

    def _validate_timeout(self, timeout_s: float) -> float:
        """Validate and normalize timeout input.

        Args:
            timeout_s: Timeout in seconds.

        Returns:
            Normalized timeout as float.

        Raises:
            TypeError: If timeout_s is not a number.
            ValueError: If timeout_s is not positive.
        """
        timeout_value = 0.0
        if isinstance(timeout_s, (int, float)):
            if timeout_s > 0:
                timeout_value = float(timeout_s)
            else:
                raise ValueError("timeout_s must be greater than zero")
        else:
            raise TypeError("timeout_s must be a number")
        return timeout_value

    def _ensure_thread_id(self) -> None:
        """Ensure a thread id is available after initialization.

        Raises:
            RuntimeError: If thread_id is missing.
        """
        if self.thread_id:
            validated_thread_id = self.thread_id
        else:
            raise RuntimeError("thread_id unavailable after start")
        return None

    def _next_request_id(self) -> int:
        """Return the next request id for a turn.

        Returns:
            Incremented request id.
        """
        self._request_id_counter += 1
        request_id = self._request_id_counter
        return request_id

    def _send_turn_start(self, request_id: int, prompt: str) -> None:
        """Send a turn/start message to the server.

        Args:
            request_id: Request identifier for the turn.
            prompt: Prompt text to send.

        Raises:
            TypeError: If request_id or prompt has invalid types.
            RuntimeError: If thread_id is missing.
        """
        if isinstance(request_id, int):
            validated_request_id = request_id
        else:
            raise TypeError("request_id must be an integer")
        if isinstance(prompt, str):
            validated_prompt = prompt
        else:
            raise TypeError("prompt must be a string")
        if self.thread_id:
            thread_identifier = self.thread_id
        else:
            raise RuntimeError("thread_id unavailable for turn start")
        self._send(
            {
                "method": METHOD_TURN_START,
                "id": validated_request_id,
                "params": {
                    "threadId": thread_identifier,
                    "input": [{"type": "text", "text": validated_prompt}],
                },
            }
        )
        return None

    def _build_deadlines(self, timeout_s: float) -> Tuple[float, float]:
        """Compute idle and hard deadlines for a turn.

        Args:
            timeout_s: Idle timeout in seconds.

        Returns:
            Tuple of (deadline, hard_deadline) timestamps.
        """
        start_time = time.time()
        deadline = start_time + timeout_s
        hard_timeout = self.environment_reader.get_float(
            ENV_HARD_TIMEOUT_S,
            DEFAULT_HARD_TIMEOUT_S,
        )
        if hard_timeout <= 0:
            hard_timeout = max(
                timeout_s * HARD_TIMEOUT_MULTIPLIER,
                timeout_s + HARD_TIMEOUT_GRACE_S,
            )
        hard_deadline = start_time + hard_timeout
        result = (deadline, hard_deadline)
        return result

    def _wait_for_turn_completion(
        self,
        request_id: int,
        timeout_s: float,
        deadline: float,
        hard_deadline: float,
    ) -> TurnResult:
        """Wait for turn completion and aggregate outputs.

        Args:
            request_id: Request id of the active turn.
            timeout_s: Idle timeout in seconds.
            deadline: Current idle deadline timestamp.
            hard_deadline: Absolute hard deadline timestamp.

        Returns:
            TurnResult containing aggregated outputs.

        Raises:
            TimeoutError: If idle or hard deadlines are exceeded.
            RuntimeError: If turn completion occurs without a result.
        """
        last_event: Dict[str, Any] = {}
        turn_result: Optional[TurnResult] = None
        current_deadline = deadline

        while turn_result is None:
            self._enforce_timeouts(current_deadline, hard_deadline)
            message = self._read_event_message()
            if message is None:
                continue

            if isinstance(message, dict):
                payload = message
            else:
                raise TypeError("message must be a dict")

            self._events_count += 1
            last_event = payload

            method_name = payload.get("method")
            if method_name:
                self._log_event(method_name)
            current_deadline = self._update_deadline(method_name, timeout_s, current_deadline)

            approval_handled = False
            if method_name and method_name.endswith(REQUEST_APPROVAL_SUFFIX):
                approval_handled = self._handle_request_approval(payload)
            if approval_handled:
                continue

            self._handle_event(payload)

            if self._is_turn_completed(payload):
                turn_result = self._build_turn_result(request_id, last_event)

        result = turn_result
        if result is None:
            raise RuntimeError(f"{self.role_name}: turn completed without result")
        return result

    def _enforce_timeouts(self, deadline: float, hard_deadline: float) -> None:
        """Raise errors if idle or hard deadlines are exceeded.

        Args:
            deadline: Current idle deadline timestamp.
            hard_deadline: Absolute hard deadline timestamp.

        Raises:
            TimeoutError: If idle or hard deadlines are exceeded.
        """
        now = time.time()
        if now >= hard_deadline:
            raise TimeoutError(f"{self.role_name}: hard timeout waiting for turn completion")
        if now >= deadline:
            raise TimeoutError(f"{self.role_name}: idle timeout waiting for turn completion")
        return None

    def _read_event_message(self) -> Optional[Dict[str, Any]]:
        """Read the next event message from the queue.

        Returns:
            Next event message or None if the queue is empty.
        """
        message: Optional[Dict[str, Any]] = None
        try:
            message = self.event_queue.get(timeout=EVENT_QUEUE_TIMEOUT_S)
        except queue.Empty:
            message = None
        return message

    def _update_deadline(self, method_name: Optional[str], timeout_s: float, current_deadline: float) -> float:
        """Update the idle deadline based on activity.

        Args:
            method_name: Event method name, if present.
            timeout_s: Idle timeout in seconds.
            current_deadline: Current idle deadline timestamp.

        Returns:
            Updated deadline timestamp.
        """
        updated_deadline = current_deadline
        if method_name and method_name not in IGNORED_TIMEOUT_METHODS:
            updated_deadline = time.time() + timeout_s
        return updated_deadline

    def _is_turn_completed(self, message: Dict[str, Any]) -> bool:
        """Check whether the message indicates turn completion.

        Args:
            message: Event message payload.

        Returns:
            True if the turn completed, otherwise False.
        """
        method_name = message.get("method")
        is_completed = method_name == METHOD_TURN_COMPLETED
        return is_completed

    def _build_turn_result(self, request_id: int, last_event: Dict[str, Any]) -> TurnResult:
        """Construct a TurnResult from buffered turn state.

        Args:
            request_id: Request id of the active turn.
            last_event: Last event payload received.

        Returns:
            TurnResult with aggregated output fields.
        """
        assistant_text = (self._assistant_text or "").strip()
        delta_text = "".join(self._delta_text_parts).strip()
        full_text = "\n\n".join(self._completed_item_text_parts).strip()
        turn_result = TurnResult(
            role=self.role_name,
            request_id=request_id,
            assistant_text=assistant_text,
            delta_text=delta_text,
            full_items_text=full_text,
            events_count=self._events_count,
            last_event=last_event,
        )
        result = turn_result
        return result
