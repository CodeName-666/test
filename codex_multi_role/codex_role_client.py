"""Client that interacts with a Codex app-server role process."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .env_utils import DEFAULT_ENVIRONMENT, EnvironmentReader
from .event_utils import DEFAULT_EVENT_PARSER, EventParser
from .logging import DEFAULT_LOGGER, TimestampLogger
from .system_utils import DEFAULT_SYSTEM_LOCATOR, SystemLocator
from .turn_result import TurnResult


FULL_ACCESS = False


@dataclass
class CodexRoleClient:
    role_name: str
    model: str
    reasoning_effort: Optional[str] = None
    auto_approve_file_changes: bool = field(
        default_factory=lambda: DEFAULT_ENVIRONMENT.get_flag(
            "CODEX_AUTO_APPROVE_FILE_CHANGES",
            "1",
        )
    )
    allow_commands: bool = field(
        default_factory=lambda: DEFAULT_ENVIRONMENT.get_flag(
            "CODEX_ALLOW_COMMANDS",
            "1",
        )
    )
    auto_approve_commands: bool = field(
        default_factory=lambda: DEFAULT_ENVIRONMENT.get_flag(
            "CODEX_AUTO_APPROVE_COMMANDS",
            "0",
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

    def start(self) -> None:
        """Start the Codex app-server process and initialize the role thread."""
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
            self.thread_id = self._wait_for_thread_id(timeout_s=15.0)
            self.logger.log(
                f"{self.role_name}: started (thread_id={self.thread_id}, model={self.model})"
            )
        return None

    def stop(self) -> None:
        """Terminate the running process if it exists."""
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
        self.process = None
        return None

    def _build_command_line(self, codex_binary: str) -> List[str]:
        """Build the command line used to start the Codex app-server."""
        command_line: List[str] = [codex_binary]
        if self.reasoning_effort:
            command_line += ["-c", f"model_reasoning_effort={json.dumps(self.reasoning_effort)}"]
        command_line.append("app-server")
        return command_line

    def _start_reader_thread(self) -> None:
        """Begin the background reader thread for server events."""
        reader_thread = threading.Thread(target=self._reader_thread_main, daemon=True)
        reader_thread.start()
        return None

    def _reader_thread_main(self) -> None:
        """Read server events from stdout and feed them into the queue."""
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
        """Persist events to a JSONL file for debugging and auditability."""
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
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": self.role_name,
                        "title": self.role_name,
                        "version": "0.1.0",
                    }
                },
            }
        )
        self._send({"method": "initialized", "params": {}})
        return None

    def _send_thread_start(self) -> None:
        """Request a new thread from the server for this role."""
        self._send({"method": "thread/start", "id": 1, "params": {"model": self.model}})
        return None

    def _send(self, message: Dict[str, Any]) -> None:
        """Send a JSON message to the server."""
        process = self.process
        if process is None or process.stdin is None:
            raise RuntimeError("process stdin unavailable; cannot send message")
        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        process.stdin.write(payload)
        process.stdin.flush()
        return None

    def _wait_for_thread_id(self, timeout_s: float) -> str:
        """Wait for the thread id response from the server."""
        deadline = time.time() + timeout_s
        thread_identifier = ""
        while time.time() < deadline and not thread_identifier:
            try:
                message = self.event_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if message.get("id") == 1:
                result = message.get("result") or {}
                thread = result.get("thread") or {}
                candidate_id = thread.get("id")
                if candidate_id:
                    thread_identifier = candidate_id
        if not thread_identifier:
            raise TimeoutError(f"{self.role_name}: timed out waiting for thread id")
        return thread_identifier

    def _reset_turn_buffers(self) -> None:
        """Clear state collected for the current turn."""
        self._delta_text_parts = []
        self._completed_item_text_parts = []
        self._assistant_text = None
        self._events_count = 0
        return None

    def _handle_event(self, message: Dict[str, Any]) -> None:
        """Process event messages to extract assistant text."""
        method_name = message.get("method") or ""
        if method_name == "item/delta":
            # Deltas arrive as partial streaming text.
            params = message.get("params") or {}
            delta = params.get("delta") or params.get("item") or {}
            if isinstance(delta, dict):
                extracted_text = self.event_parser.extract_text_from_item(delta)
                if extracted_text:
                    self._delta_text_parts.append(extracted_text)
        elif method_name == "item/completed":
            # Completed items may include the final assistant text.
            item = (message.get("params") or {}).get("item") or {}
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
        """Send approval responses for requests that require user consent."""
        self._send({"id": request_id, "result": {"approved": approved}})
        return None

    def _handle_request_approval(self, message: Dict[str, Any]) -> bool:
        """Approve or reject tool/file requests based on configuration."""
        method_name = (message.get("method") or "").strip()
        handled = False

        if method_name.endswith("/requestApproval"):
            handled = True
            request_id = message.get("id")

            if FULL_ACCESS:
                # Full access bypasses any approval gate.
                if request_id is not None:
                    self._send_approval_response(request_id, True)
                    self.logger.log(
                        f"[{self.role_name}] auto-approved approval request "
                        f"(id={request_id}, method={method_name})"
                    )
            elif method_name == "item/commandExecution/requestApproval":
                # Commands may require explicit approval, based on configuration.
                if request_id is None:
                    handled = True
                elif not self.allow_commands:
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
            elif method_name == "item/fileChange/requestApproval" and self.auto_approve_file_changes:
                # File changes can be auto-approved when allowed.
                if request_id is not None:
                    self._send_approval_response(request_id, True)
                    self.logger.log(
                        f"[{self.role_name}] auto-approved file change request (id={request_id})"
                    )
            else:
                raise RuntimeError(
                    f"{self.role_name}: approval required for {method_name}. "
                    "Set FULL_ACCESS=True or CODEX_AUTO_APPROVE_FILE_CHANGES=1."
                )

        return handled

    def _log_event(self, method_name: str) -> None:
        """Log server events that are helpful for tracing progress."""
        self.logger.log(f"[{self.role_name}] event: {method_name}")
        return None

    def run_turn(self, prompt: str, timeout_s: float = 180.0) -> TurnResult:
        """Run a single Codex turn and return the aggregated results."""
        self.start()
        if not self.thread_id:
            raise RuntimeError("thread_id unavailable after start")

        self._request_id_counter += 1
        request_id = self._request_id_counter
        self._reset_turn_buffers()

        self._send(
            {
                "method": "turn/start",
                "id": request_id,
                "params": {
                    "threadId": self.thread_id,
                    "input": [{"type": "text", "text": prompt}],
                },
            }
        )

        start_time = time.time()
        deadline = start_time + timeout_s
        hard_timeout = self.environment_reader.get_float("HARD_TIMEOUT_S", "0")
        if hard_timeout <= 0:
            hard_timeout = max(timeout_s * 3, timeout_s + 300)
        hard_deadline = start_time + hard_timeout

        last_event: Dict[str, Any] = {}
        turn_result: Optional[TurnResult] = None
        ignored_timeout_methods = {
            "thread/tokenUsage/updated",
            "account/rateLimits/updated",
            "codex/event/token_count",
        }

        while True:
            now = time.time()
            if now >= hard_deadline:
                raise TimeoutError(f"{self.role_name}: hard timeout waiting for turn completion")
            if now >= deadline:
                raise TimeoutError(f"{self.role_name}: idle timeout waiting for turn completion")
            try:
                message = self.event_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            self._events_count += 1
            last_event = message

            method_name = message.get("method")
            if method_name:
                self._log_event(method_name)
            if method_name and method_name not in ignored_timeout_methods:
                deadline = time.time() + timeout_s

            approval_handled = False
            if method_name and method_name.endswith("/requestApproval"):
                approval_handled = self._handle_request_approval(message)
            if approval_handled:
                continue

            self._handle_event(message)

            if message.get("method") == "turn/completed":
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
                break

        if turn_result is None:
            raise RuntimeError(f"{self.role_name}: turn completed without result")

        return turn_result
