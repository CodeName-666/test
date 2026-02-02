"""Client that interacts with a Codex app-server role process."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from defaults import (
    DEFAULT_ALLOW_COMMANDS,
    DEFAULT_AUTO_APPROVE_COMMANDS,
    DEFAULT_AUTO_APPROVE_FILE_CHANGES,
    ENV_ALLOW_COMMANDS,
    ENV_AUTO_APPROVE_COMMANDS,
    ENV_AUTO_APPROVE_FILE_CHANGES,
    FULL_ACCESS,
)
from ..utils.event_utils import EventParser
from ..utils.system_utils import SystemLocator
from .role_client import RoleClient
from .role_transport import AppServerTransport, RoleTransport

INIT_REQUEST_ID = 0
THREAD_START_REQUEST_ID = 1
THREAD_ID_TIMEOUT_S = 15.0

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
class CodexRoleClient(RoleClient):
    """Client that manages a Codex role process and turn lifecycle.

    Extends the provider-agnostic ``RoleClient`` with Codex app-server
    specific initialization, event handling, and approval flow.

    Attributes:
        auto_approve_file_changes: Whether to auto-approve file change requests.
        allow_commands: Whether command execution requests are allowed.
        auto_approve_commands: Whether to auto-approve command requests.
        system_locator: System locator for resolving CLI binaries.
        thread_id: Active thread identifier for the role session.

    Raises:
        TypeError: If constructed with invalid field types.
    """

    event_parser: EventParser = field(
        default_factory=EventParser)
    auto_approve_file_changes: Optional[bool] = None
    allow_commands: Optional[bool] = None
    auto_approve_commands: Optional[bool] = None
    system_locator: SystemLocator = field(
        default_factory=SystemLocator)
    thread_id: Optional[str] = None
    _delta_text_parts: List[str] = field(default_factory=list)
    _completed_item_text_parts: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate Codex-specific fields after base initialization.

        Raises:
            TypeError: If any field has an invalid type.
            ValueError: If required string fields are empty.
        """
        super().__post_init__()
        self._validate_instance(self.event_parser, EventParser, "event_parser")
        self._resolve_approval_flags()
        self._validate_bool(self.auto_approve_file_changes,
                            "auto_approve_file_changes")
        self._validate_bool(self.allow_commands, "allow_commands")
        self._validate_bool(self.auto_approve_commands,
                            "auto_approve_commands")
        self._validate_instance(self.system_locator,
                                SystemLocator, "system_locator")
        return None

    def _resolve_approval_flags(self) -> None:
        """Resolve approval flags from environment_reader when not explicitly set.

        Uses the injected ``self.environment_reader`` instead of a global
        singleton so that per-instance or test overrides are respected.
        """
        if self.auto_approve_file_changes is None:
            self.auto_approve_file_changes = self.environment_reader.get_flag(
                ENV_AUTO_APPROVE_FILE_CHANGES,
                DEFAULT_AUTO_APPROVE_FILE_CHANGES,
            )
        if self.allow_commands is None:
            self.allow_commands = self.environment_reader.get_flag(
                ENV_ALLOW_COMMANDS,
                DEFAULT_ALLOW_COMMANDS,
            )
        if self.auto_approve_commands is None:
            self.auto_approve_commands = self.environment_reader.get_flag(
                ENV_AUTO_APPROVE_COMMANDS,
                DEFAULT_AUTO_APPROVE_COMMANDS,
            )
        return None

    # ------------------------------------------------------------------
    # Abstract hook implementations
    # ------------------------------------------------------------------

    def _resolve_transport(self) -> RoleTransport:
        """Create an AppServerTransport or validate the provided transport."""
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

    def _send_initialization(self) -> None:
        """Send the Codex initialize/initialized handshake messages."""
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

    def _establish_session(self) -> None:
        """Request a Codex thread and wait for the thread id."""
        self._send(
            {
                "method": METHOD_THREAD_START,
                "id": THREAD_START_REQUEST_ID,
                "params": {"model": self.model},
            }
        )
        self.thread_id = self._wait_for_thread_id(
            timeout_s=THREAD_ID_TIMEOUT_S)
        self.logger.log(
            f"{self.role_name}: started (thread_id={self.thread_id}, model={self.model})"
        )
        return None

    def _clear_session_state(self) -> None:
        """Clear the Codex thread id."""
        self.thread_id = None
        return None

    def _ensure_session_ready(self) -> None:
        """Ensure a thread id is available after initialization.

        Raises:
            RuntimeError: If thread_id is missing.
        """
        if self.thread_id:
            validated_thread_id = self.thread_id
        else:
            raise RuntimeError("thread_id unavailable after start")
        return None

    def _reset_turn_buffers(self) -> None:
        """Clear Codex-specific per-turn buffers."""
        self._delta_text_parts = []
        self._completed_item_text_parts = []
        return None

    def _send_turn_start(self, request_id: int, prompt: str) -> None:
        """Send a Codex turn/start message to the server.

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

    def _handle_event(self, message: Dict[str, Any]) -> None:
        """Process Codex event messages to extract assistant text.

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

    def _is_approval_request(self, message: Dict[str, Any]) -> bool:
        """Check whether the message is a Codex approval request.

        Args:
            message: Event message payload.

        Returns:
            True if the message is an approval request.
        """
        method_name = (message.get("method") or "").strip()
        is_approval = method_name.endswith(REQUEST_APPROVAL_SUFFIX)
        return is_approval

    def _handle_approval_request(self, message: Dict[str, Any]) -> bool:
        """Approve or reject Codex tool/file requests based on configuration.

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

    def _is_turn_completed(self, message: Dict[str, Any]) -> bool:
        """Check whether the message indicates Codex turn completion.

        Args:
            message: Event message payload.

        Returns:
            True if the turn completed, otherwise False.
        """
        method_name = message.get("method")
        is_completed = method_name == METHOD_TURN_COMPLETED
        return is_completed

    def _collect_turn_texts(self) -> Tuple[str, str, str]:
        """Collect Codex-specific text outputs after turn completion.

        Returns:
            Tuple of (assistant_text, delta_text, full_items_text).
        """
        assistant_text = (self._assistant_text or "").strip()
        delta_text = "".join(self._delta_text_parts).strip()
        full_text = "\n\n".join(self._completed_item_text_parts).strip()
        result = (assistant_text, delta_text, full_text)
        return result

    def _is_ignored_for_timeout(self, method_name: Optional[str]) -> bool:
        """Whether this Codex event should not reset the idle deadline.

        Args:
            method_name: Event method name, may be None.

        Returns:
            True if the event should be ignored for timeout purposes.
        """
        if method_name is None:
            return False
        is_ignored = method_name in IGNORED_TIMEOUT_METHODS
        return is_ignored

    # ------------------------------------------------------------------
    # Codex-specific private helpers
    # ------------------------------------------------------------------

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
