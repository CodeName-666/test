"""Transport abstractions for Codex role communication."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from ..utils.event_utils import EventParser
from ..utils.system_utils import SystemLocator
from ..utils.validation_utils import ValidationMixin


class RoleTransport(Protocol):
    """Interface for transports that exchange JSON events with a role process."""

    def start(self) -> None:
        """Start the transport and begin receiving events."""

    def stop(self) -> None:
        """Stop the transport and release resources."""

    def send(self, message: Dict[str, Any]) -> None:
        """Send a JSON message over the transport."""

    def read_event(self, timeout_s: float) -> Optional[Dict[str, Any]]:
        """Read the next event message or return None on timeout."""

    def set_events_file(self, events_file: Optional[Path]) -> None:
        """Set the optional JSONL events log file."""


@dataclass
class AppServerTransport(ValidationMixin):
    """Transport implementation using the local codex app-server process."""

    role_name: str
    model: str
    reasoning_effort: Optional[str] = None
    event_parser: EventParser = field(default_factory=EventParser)
    system_locator: SystemLocator = field(default_factory=SystemLocator)
    events_file: Optional[Path] = None
    process: Optional[subprocess.Popen] = None
    event_queue: "queue.Queue[Dict[str, Any]]" = field(default_factory=queue.Queue)

    def __post_init__(self) -> None:
        """Validate transport fields after initialization.

        Raises:
            TypeError: If field types are invalid.
            ValueError: If required string fields are empty.
        """
        self._validate_non_empty_str(self.role_name, "role_name")
        self._validate_non_empty_str(self.model, "model")
        self._validate_optional_non_empty_str(self.reasoning_effort, "reasoning_effort")
        self._validate_instance(self.event_parser, EventParser, "event_parser")
        self._validate_instance(self.system_locator, SystemLocator, "system_locator")
        self._validate_optional_instance(self.events_file, Path, "events_file")
        self._validate_instance(self.event_queue, queue.Queue, "event_queue", "queue.Queue")
        return None

    def set_events_file(self, events_file: Optional[Path]) -> None:
        """Set the JSONL file for raw event logging.

        Args:
            events_file: File path to write events to, or None to disable.

        Raises:
            TypeError: If events_file is not a pathlib.Path or None.
        """
        if events_file is None:
            validated_path = None
        elif isinstance(events_file, Path):
            validated_path = events_file
        else:
            raise TypeError("events_file must be a pathlib.Path or None")
        self.events_file = validated_path
        return None

    def start(self) -> None:
        """Start the app-server process and background reader thread.

        Raises:
            RuntimeError: If codex CLI is not found in PATH.
        """
        should_start = self.process is None
        if should_start:
            codex_binary = self.system_locator.find_codex()
            if not codex_binary:
                raise RuntimeError("codex CLI not found in PATH")
            command_line = self._build_command_line(codex_binary)
            self.process = subprocess.Popen(
                command_line,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            self._start_reader_thread()
        return None

    def stop(self) -> None:
        """Stop the transport and terminate the subprocess if present."""
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
        self.process = None
        return None

    def send(self, message: Dict[str, Any]) -> None:
        """Send a JSON message to the app-server process.

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

    def read_event(self, timeout_s: float) -> Optional[Dict[str, Any]]:
        """Read the next event message from the queue.

        Args:
            timeout_s: Timeout in seconds to wait for an event.

        Returns:
            Parsed event dictionary or None if no event is available.

        Raises:
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
        message: Optional[Dict[str, Any]] = None
        try:
            message = self.event_queue.get(timeout=timeout_value)
        except queue.Empty:
            message = None
        result = message
        return result

    def _start_reader_thread(self) -> None:
        """Start the background thread that reads events."""
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
        result = command_line
        return result
