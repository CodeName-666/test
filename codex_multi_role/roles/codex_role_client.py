"""Client that interacts with a Codex app-server role process."""
from __future__ import annotations

import time
from functools import partial
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
from ..utils.env_utils import EnvironmentReader
from ..utils.event_utils import EventParser
from ..logging import TimestampLogger
from ..utils.system_utils import SystemLocator
from ..turn_result import TurnResult
from ..utils.validation_utils import ValidationMixin
from .role_transport import AppServerTransport, RoleTransport

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


def _resolve_default_flag(env_key: str, default_value: str) -> bool:
    """Resolve a boolean flag from environment defaults.

    Args:
        env_key: Environment variable name to read.
        default_value: Default value when env_key is unset.

    Returns:
        Boolean flag value.

    Raises:
        TypeError: If env_key or default_value is not a string.
        ValueError: If env_key or default_value is empty.
    """
    if isinstance(env_key, str):
        if env_key.strip():
            normalized_env = env_key
        else:
            raise ValueError("env_key must not be empty")
    else:
        raise TypeError("env_key must be a string")
    if isinstance(default_value, str):
        if default_value.strip():
            normalized_default = default_value
        else:
            raise ValueError("default_value must not be empty")
    else:
        raise TypeError("default_value must be a string")

    result = DEFAULT_ENVIRONMENT.get_flag(normalized_env, normalized_default)
    return result


@dataclass
class CodexRoleClient(ValidationMixin):
    """Client that manages a Codex role process and turn lifecycle.

    Attributes:
        role_name: Role name used to label events and logs.
        model: Model name requested from the Codex CLI.
        reasoning_effort: Optional reasoning effort label for the model.
        auto_approve_file_changes: Whether to auto-approve file change requests.
        allow_commands: Whether command execution requests are allowed.
        auto_approve_commands: Whether to auto-approve command requests.
        transport: Transport used to communicate with the role process.
        environment_reader: Environment reader for configuration.
        event_parser: Parser for event stream messages.
        logger: Logger instance for trace output.
        system_locator: System locator for resolving CLI binaries.
        thread_id: Active thread identifier for the role session.
        events_file: Optional JSONL file path for event logging.

    Raises:
        TypeError: If constructed with invalid field types.
    """
    role_name: str
    model: str
    reasoning_effort: Optional[str] = None
    auto_approve_file_changes: bool = field(
        default_factory=partial(
            _resolve_default_flag,
            ENV_AUTO_APPROVE_FILE_CHANGES,
            DEFAULT_AUTO_APPROVE_FILE_CHANGES,
        ),
    )
    allow_commands: bool = field(
        default_factory=partial(
            _resolve_default_flag,
            ENV_ALLOW_COMMANDS,
            DEFAULT_ALLOW_COMMANDS,
        ),
    )
    auto_approve_commands: bool = field(
        default_factory=partial(
            _resolve_default_flag,
            ENV_AUTO_APPROVE_COMMANDS,
            DEFAULT_AUTO_APPROVE_COMMANDS,
        ),
    )
    transport: Optional[RoleTransport] = None
    environment_reader: EnvironmentReader = field(
        default_factory=lambda: DEFAULT_ENVIRONMENT)
    event_parser: EventParser = field(
        default_factory=lambda: DEFAULT_EVENT_PARSER)
    logger: TimestampLogger = field(default_factory=lambda: DEFAULT_LOGGER)
    system_locator: SystemLocator = field(
        default_factory=lambda: DEFAULT_SYSTEM_LOCATOR)

    _events_file: Optional[Path] = field(default=None, init=False)
    thread_id: Optional[str] = None
    _request_id_counter: int = 100
    _delta_text_parts: List[str] = field(default_factory=list)
    _completed_item_text_parts: List[str] = field(default_factory=list)
    _assistant_text: Optional[str] = None
    _events_count: int = 0
    _transport_started: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Validate core dataclass fields after initialization.

        Raises:
            TypeError: If any field has an invalid type.
            ValueError: If required string fields are empty.
        """
        self._validate_non_empty_str(self.role_name, "role_name")
        self._validate_non_empty_str(self.model, "model")
        self._validate_optional_non_empty_str(
            self.reasoning_effort,
            "reasoning_effort",
            empty_message="reasoning_effort must not be empty when provided",
        )
        self._validate_bool(self.auto_approve_file_changes,
                            "auto_approve_file_changes")
        self._validate_bool(self.allow_commands, "allow_commands")
        self._validate_bool(self.auto_approve_commands,
                            "auto_approve_commands")
        self._validate_instance(self.environment_reader,
                                EnvironmentReader, "environment_reader")
        self._validate_instance(self.event_parser, EventParser, "event_parser")
        self._validate_instance(self.logger, TimestampLogger, "logger")
        self._validate_instance(self.system_locator,
                                SystemLocator, "system_locator")
        self.transport = self._resolve_transport()
        self._transport_started = False
        self._events_file = None
        self._sync_transport_events_file()
        return None

    @property
    def events_file(self) -> Optional[Path]:
        """Return the events log file path, if set."""
        result = self._events_file
        return result

    @events_file.setter
    def events_file(self, value: Optional[Path]) -> None:
        """Set the events log file path and propagate to the transport."""
        if value is None:
            validated_path = None
        elif isinstance(value, Path):
            validated_path = value
        else:
            raise TypeError("events_file must be a pathlib.Path or None")
        self._events_file = validated_path
        self._sync_transport_events_file()
        return None

    def _sync_transport_events_file(self) -> None:
        """Keep the transport event log path in sync with the client."""
        transport = self._require_transport()
        transport.set_events_file(self._events_file)
        return None

    def _resolve_transport(self) -> RoleTransport:
        """Resolve and validate the transport implementation."""
        transport_value = self.transport
        if transport_value is None:
            transport_value = AppServerTransport(
                role_name=self.role_name,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                event_parser=self.event_parser,
                system_locator=self.system_locator,
            )
        else:
            transport_value = self._validate_transport(transport_value)
        result = transport_value
        return result

    def _validate_transport(self, transport_value: Any) -> RoleTransport:
        """Validate that the transport exposes the required interface."""
        if transport_value is None:
            raise TypeError("transport must not be None")
        required_methods = ("start", "stop", "send",
                            "read_event", "set_events_file")
        missing: List[str] = []
        for method_name in required_methods:
            candidate = getattr(transport_value, method_name, None)
            if not callable(candidate):
                missing.append(method_name)
        if missing:
            raise TypeError(f"transport missing methods: {', '.join(missing)}")
        result = transport_value
        return result

    def _require_transport(self) -> RoleTransport:
        """Return the transport instance, enforcing it is present."""
        transport_value = self.transport
        if transport_value is None:
            raise RuntimeError("transport unavailable")
        result = transport_value
        return result

    def start(self) -> None:
        """Start the Codex app-server process and initialize the role thread.

        Side Effects:
            Starts the transport, initializes the role thread, and starts event streaming.

        Raises:
            RuntimeError: If codex CLI is not found in PATH.
            RuntimeError: If thread id cannot be obtained.
        """
        should_start = not self._transport_started
        if should_start:
            self._start_transport()
            self._send_initialize_messages()

            self._send_thread_start()
            self.thread_id = self._wait_for_thread_id(
                timeout_s=THREAD_ID_TIMEOUT_S)
            self.logger.log(
                f"{self.role_name}: started (thread_id={self.thread_id}, model={self.model})"
            )
        return None

    def stop(self) -> None:
        """Stop the running transport if it exists.

        Side Effects:
            Stops the transport and clears internal state.
        """
        self._stop_transport()
        self.thread_id = None
        return None

    def _start_transport(self) -> None:
        """Start the configured transport."""
        transport = self._require_transport()
        transport.start()
        self._transport_started = True
        return None

    def _stop_transport(self) -> None:
        """Stop the configured transport."""
        transport = self._require_transport()
        transport.stop()
        self._transport_started = False
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
            RuntimeError: If transport is unavailable.
        """
        if isinstance(message, dict):
            payload_message = message
        else:
            raise TypeError("message must be a dict")
        transport = self._require_transport()
        transport.send(payload_message)
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
            message = self._read_event_message()
            if message is None:
                continue
            if isinstance(message, dict):
                if message.get("id") == THREAD_START_REQUEST_ID:
                    result = message.get("result") or {}
                    thread = result.get("thread") or {}
                    candidate_id = thread.get("id")
                    if candidate_id:
                        thread_identifier = candidate_id
            else:
                raise TypeError("message must be a dict")
        if thread_identifier:
            result = thread_identifier
        else:
            raise TimeoutError(
                f"{self.role_name}: timed out waiting for thread id")
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
                extracted_text = self.event_parser.extract_text_from_item(
                    delta)
                if extracted_text:
                    self._delta_text_parts.append(extracted_text)
        elif method_name == METHOD_ITEM_COMPLETED:
            # Completed items may include the final assistant text.
            item = (payload.get("params") or {}).get("item") or {}
            if isinstance(item, dict):
                item_type_name = self.event_parser.normalize_item_type_name(
                    item.get("type"))
                extracted_text = self.event_parser.extract_text_from_item(item)
                raw_type = item.get("type") or "unknown"
                if extracted_text:
                    self._completed_item_text_parts.append(
                        f"[{raw_type}] {extracted_text}")
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
        self._send({"id": validated_request_id, "result": {
                   "approved": validated_approved}})
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
            current_deadline = self._update_deadline(
                method_name, timeout_s, current_deadline)

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
            raise RuntimeError(
                f"{self.role_name}: turn completed without result")
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
            raise TimeoutError(
                f"{self.role_name}: hard timeout waiting for turn completion")
        if now >= deadline:
            raise TimeoutError(
                f"{self.role_name}: idle timeout waiting for turn completion")
        return None

    def _read_event_message(self) -> Optional[Dict[str, Any]]:
        """Read the next event message from the transport.

        Returns:
            Next event message or None if no event is available.
        """
        transport = self._require_transport()
        message = transport.read_event(EVENT_QUEUE_TIMEOUT_S)
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
