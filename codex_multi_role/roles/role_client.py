"""Abstract base class for provider-agnostic role clients."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from defaults import (
    DEFAULT_HARD_TIMEOUT_S,
    ENV_HARD_TIMEOUT_S,
)
from ..utils.env_utils import EnvironmentReader
from ..utils.event_utils import EventParser
from ..logging import TimestampLogger
from ..turn_result import TurnResult
from ..utils.validation_utils import ValidationMixin
from .role_transport import RoleTransport

EVENT_QUEUE_TIMEOUT_S = 0.2
HARD_TIMEOUT_MULTIPLIER = 3.0
HARD_TIMEOUT_GRACE_S = 300.0


@dataclass
class RoleClient(ABC, ValidationMixin):
    """Abstract base class managing the provider-agnostic turn lifecycle.

    Subclasses implement provider-specific hooks (initialization, event
    handling, approval flow, session management) while this class owns
    the shared turn lifecycle, timeout enforcement, and result aggregation.

    Attributes:
        role_name: Role name used to label events and logs.
        model: Model name requested from the provider.
        reasoning_effort: Optional reasoning effort label for the model.
        transport: Transport used to communicate with the role process.
        environment_reader: Environment reader for configuration.
        event_parser: Parser for event stream messages.
        logger: Logger instance for trace output.

    Raises:
        TypeError: If constructed with invalid field types.
    """

    # --- Required fields (no defaults) ---
    role_name: str
    model: str

    # --- Optional fields with defaults ---
    reasoning_effort: Optional[str] = None
    transport: Optional[RoleTransport] = None
    environment_reader: EnvironmentReader = field(
        default_factory=EnvironmentReader)
    event_parser: EventParser = field(
        default_factory=EventParser)
    logger: TimestampLogger = field(default_factory=TimestampLogger)

    # --- Internal state ---
    _events_file: Optional[Path] = field(default=None, init=False)
    _request_id_counter: int = 100
    _assistant_text: Optional[str] = None
    _events_count: int = 0
    _transport_started: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Validate common dataclass fields after initialization.

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
        self._validate_instance(self.environment_reader,
                                EnvironmentReader, "environment_reader")
        self._validate_instance(self.event_parser, EventParser, "event_parser")
        self._validate_instance(self.logger, TimestampLogger, "logger")
        self.transport = self._resolve_transport()
        self._transport_started = False
        self._events_file = None
        self._sync_transport_events_file()
        return None

    # ------------------------------------------------------------------
    # Abstract hooks – subclasses must implement
    # ------------------------------------------------------------------

    @abstractmethod
    def _resolve_transport(self) -> RoleTransport:
        """Create or validate the transport implementation.

        Called during ``__post_init__``.  When ``self.transport`` is *None*
        the subclass should create its default transport; otherwise it
        should validate the provided instance.

        Returns:
            A valid RoleTransport implementation.
        """
        ...

    @abstractmethod
    def _send_initialization(self) -> None:
        """Send provider-specific initialization messages after transport starts.

        Called by ``start()``.
        """
        ...

    @abstractmethod
    def _establish_session(self) -> None:
        """Establish a provider-specific session after initialization.

        Called by ``start()`` after ``_send_initialization()``.
        """
        ...

    @abstractmethod
    def _clear_session_state(self) -> None:
        """Clear provider-specific session state during ``stop()``."""
        ...

    @abstractmethod
    def _ensure_session_ready(self) -> None:
        """Verify that the session is ready before a turn.

        Called by ``run_turn()`` before sending the turn request.

        Raises:
            RuntimeError: If the session is not ready.
        """
        ...

    @abstractmethod
    def _reset_turn_buffers(self) -> None:
        """Clear provider-specific per-turn state before a new turn.

        Base-class state (``_events_count``, ``_assistant_text``) is
        already reset before this hook is called.
        """
        ...

    @abstractmethod
    def _send_turn_start(self, request_id: int, prompt: str) -> None:
        """Send the provider-specific turn-start message.

        Args:
            request_id: Request identifier for the turn.
            prompt: Prompt text to send.
        """
        ...

    @abstractmethod
    def _handle_event(self, message: Dict[str, Any]) -> None:
        """Process a single event message and accumulate text.

        Args:
            message: Event message payload.
        """
        ...

    @abstractmethod
    def _is_approval_request(self, message: Dict[str, Any]) -> bool:
        """Check whether the message is an approval request.

        Args:
            message: Event message payload.

        Returns:
            True if the message is an approval request.
        """
        ...

    @abstractmethod
    def _handle_approval_request(self, message: Dict[str, Any]) -> bool:
        """Handle approval-gated requests.

        Args:
            message: Event message payload.

        Returns:
            True if the message was handled as an approval request.
        """
        ...

    @abstractmethod
    def _is_turn_completed(self, message: Dict[str, Any]) -> bool:
        """Check whether the message signals turn completion.

        Args:
            message: Event message payload.

        Returns:
            True if the turn completed.
        """
        ...

    @abstractmethod
    def _collect_turn_texts(self) -> Tuple[str, str, str]:
        """Collect the three text outputs after turn completion.

        Returns:
            Tuple of (assistant_text, delta_text, full_items_text).
        """
        ...

    @abstractmethod
    def _is_ignored_for_timeout(self, method_name: Optional[str]) -> bool:
        """Whether this event method should *not* reset the idle deadline.

        Args:
            method_name: Event method name, may be None.

        Returns:
            True if the event should be ignored for timeout purposes.
        """
        ...

    # ------------------------------------------------------------------
    # Events-file property
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Transport helpers
    # ------------------------------------------------------------------

    def _validate_transport(self, transport_value: Any) -> RoleTransport:
        """Validate that the transport exposes the required interface.

        Args:
            transport_value: Transport candidate to validate.

        Returns:
            Validated RoleTransport instance.

        Raises:
            TypeError: If transport_value is None or missing methods.
        """
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
        """Return the transport instance, enforcing it is present.

        Returns:
            The current RoleTransport instance.

        Raises:
            RuntimeError: If transport is unavailable.
        """
        transport_value = self.transport
        if transport_value is None:
            raise RuntimeError("transport unavailable")
        result = transport_value
        return result

    # ------------------------------------------------------------------
    # Lifecycle: start / stop
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the transport and initialize the provider session.

        Side Effects:
            Starts the transport, sends initialization messages, and
            establishes the provider session.

        Raises:
            RuntimeError: If the provider binary is not found or the
                session cannot be established.
        """
        should_start = not self._transport_started
        if should_start:
            self._start_transport()
            self._send_initialization()
            self._establish_session()
        return None

    def stop(self) -> None:
        """Stop the running transport and clear session state.

        Side Effects:
            Stops the transport and clears provider-specific state.
        """
        self._stop_transport()
        self._clear_session_state()
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

    # ------------------------------------------------------------------
    # Messaging helpers
    # ------------------------------------------------------------------

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

    def _read_event_message(self) -> Optional[Dict[str, Any]]:
        """Read the next event message from the transport.

        Returns:
            Next event message or None if no event is available.
        """
        transport = self._require_transport()
        message = transport.read_event(EVENT_QUEUE_TIMEOUT_S)
        return message

    # ------------------------------------------------------------------
    # Turn execution
    # ------------------------------------------------------------------

    def run_turn(self, prompt: str, timeout_s: float = 180.0) -> TurnResult:
        """Run a single turn and return the aggregated results.

        Args:
            prompt: Prompt text sent to the role.
            timeout_s: Idle timeout in seconds for turn completion.

        Returns:
            TurnResult containing aggregated assistant output.

        Raises:
            TypeError: If prompt/timeout_s have invalid types.
            ValueError: If timeout_s is not positive.
            RuntimeError: If the session is unavailable or the turn
                fails to complete.
            TimeoutError: If idle or hard timeouts are exceeded.
        """
        self._validate_prompt(prompt)
        timeout_value = self._validate_timeout(timeout_s)
        self.start()
        self._ensure_session_ready()

        request_id = self._next_request_id()

        # Reset base-class state before subclass buffers.
        self._assistant_text = None
        self._events_count = 0
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

    def _next_request_id(self) -> int:
        """Return the next request id for a turn.

        Returns:
            Incremented request id.
        """
        self._request_id_counter += 1
        request_id = self._request_id_counter
        return request_id

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

    # ------------------------------------------------------------------
    # Event loop
    # ------------------------------------------------------------------

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

            if self._is_approval_request(payload):
                handled = self._handle_approval_request(payload)
                if handled:
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
        if method_name and not self._is_ignored_for_timeout(method_name):
            updated_deadline = time.time() + timeout_s
        return updated_deadline

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

    def _build_turn_result(self, request_id: int, last_event: Dict[str, Any]) -> TurnResult:
        """Construct a TurnResult from buffered turn state.

        Args:
            request_id: Request id of the active turn.
            last_event: Last event payload received.

        Returns:
            TurnResult with aggregated output fields.
        """
        assistant_text, delta_text, full_text = self._collect_turn_texts()
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
