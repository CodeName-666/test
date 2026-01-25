"""Client that interacts with a Codex app-server role process."""
import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .env_utils import env_flag, env_float
from .event_utils import extract_text_from_item, normalize_item_type, safe_event_json
from .logging import log
from .system_utils import find_codex
from .turn_result import TurnResult


FULL_ACCESS = False


@dataclass
class CodexRoleClient:
    role_name: str
    model: str
    reasoning_effort: Optional[str] = None
    auto_approve_file_changes: bool = field(default_factory=lambda: env_flag("CODEX_AUTO_APPROVE_FILE_CHANGES", "1"))
    allow_commands: bool = field(default_factory=lambda: env_flag("CODEX_ALLOW_COMMANDS", "1"))
    auto_approve_commands: bool = field(default_factory=lambda: env_flag("CODEX_AUTO_APPROVE_COMMANDS", "0"))

    proc: Optional[subprocess.Popen] = None
    inbox: "queue.Queue[Dict[str, Any]]" = field(default_factory=queue.Queue)
    thread_id: Optional[str] = None
    _req_id: int = 100
    events_file: Optional[Path] = None
    _delta_parts: List[str] = field(default_factory=list)
    _completed_text_parts: List[str] = field(default_factory=list)
    _assistant_text: Optional[str] = None
    _events_count: int = 0

    def start(self) -> None:
        if self.proc is not None:
            return

        codex_bin = find_codex()
        if not codex_bin:
            raise RuntimeError("codex CLI not found in PATH")

        cmd = [codex_bin]
        if self.reasoning_effort:
            cmd += ["-c", f"model_reasoning_effort={json.dumps(self.reasoning_effort)}"]
        cmd += ["app-server"]

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )

        def reader() -> None:
            assert self.proc and self.proc.stdout
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                msg = safe_event_json(line)
                if msg is None:
                    continue
                if self.events_file:
                    try:
                        with self.events_file.open("a", encoding="utf-8") as f:
                            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                self.inbox.put(msg)

        threading.Thread(target=reader, daemon=True).start()

        self._send({
            "method": "initialize",
            "id": 0,
            "params": {"clientInfo": {"name": self.role_name, "title": self.role_name, "version": "0.1.0"}},
        })
        self._send({"method": "initialized", "params": {}})

        self._send({"method": "thread/start", "id": 1, "params": {"model": self.model}})
        self.thread_id = self._wait_for_thread_id(timeout_s=15.0)
        log(f"{self.role_name}: started (thread_id={self.thread_id}, model={self.model})")

    def stop(self) -> None:
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.proc = None

    def _send(self, msg: Dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        payload = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()

    def _wait_for_thread_id(self, timeout_s: float) -> str:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                msg = self.inbox.get(timeout=0.2)
            except queue.Empty:
                continue
            if msg.get("id") == 1:
                tid = (msg.get("result") or {}).get("thread", {}).get("id")
                if tid:
                    return tid
        raise TimeoutError(f"{self.role_name}: timed out waiting for thread id")

    def _reset_turn_buffers(self) -> None:
        self._delta_parts = []
        self._completed_text_parts = []
        self._assistant_text = None
        self._events_count = 0

    def _handle_event(self, msg: Dict[str, Any]) -> None:
        method = msg.get("method") or ""
        if method == "item/delta":
            params = msg.get("params") or {}
            delta = params.get("delta") or params.get("item") or {}
            if isinstance(delta, dict):
                txt = extract_text_from_item(delta)
                if txt:
                    self._delta_parts.append(txt)
        elif method == "item/completed":
            item = (msg.get("params") or {}).get("item") or {}
            if not isinstance(item, dict):
                return
            item_type = normalize_item_type(item.get("type"))
            txt = extract_text_from_item(item)
            raw_type = item.get("type") or "unknown"
            if txt:
                self._completed_text_parts.append(f"[{raw_type}] {txt}")
            if item_type in ("agentmessage", "assistantmessage") and txt:
                self._assistant_text = txt

    def _handle_request_approval(self, msg: Dict[str, Any]) -> bool:
        method = (msg.get("method") or "").strip()
        if not method.endswith("/requestApproval"):
            return False

        if FULL_ACCESS:
            req_id = msg.get("id")
            if req_id is not None:
                self._send({"id": req_id, "result": {"approved": True}})
                log(f"[{self.role_name}] auto-approved approval request (id={req_id}, method={method})")
                return True

        if method == "item/commandExecution/requestApproval":
            req_id = msg.get("id")
            if req_id is None:
                return True
            if not self.allow_commands:
                self._send({"id": req_id, "result": {"approved": False}})
                log(f"[{self.role_name}] denied command execution (CODEX_ALLOW_COMMANDS=0)")
                return True
            if self.auto_approve_commands:
                self._send({"id": req_id, "result": {"approved": True}})
                log(f"[{self.role_name}] auto-approved command execution (id={req_id})")
                return True
            raise RuntimeError(
                f"{self.role_name}: approval required for {method}. "
                "Set FULL_ACCESS=True, or CODEX_AUTO_APPROVE_COMMANDS=1, or disable via CODEX_ALLOW_COMMANDS=0."
            )

        if method == "item/fileChange/requestApproval" and self.auto_approve_file_changes:
            req_id = msg.get("id")
            if req_id is not None:
                self._send({"id": req_id, "result": {"approved": True}})
                log(f"[{self.role_name}] auto-approved file change request (id={req_id})")
                return True

        raise RuntimeError(
            f"{self.role_name}: approval required for {method}. "
            "Set FULL_ACCESS=True or CODEX_AUTO_APPROVE_FILE_CHANGES=1."
        )

    def run_turn(self, prompt: str, timeout_s: float = 180.0) -> TurnResult:
        self.start()
        assert self.thread_id

        self._req_id += 1
        req_id = self._req_id
        self._reset_turn_buffers()

        self._send({
            "method": "turn/start",
            "id": req_id,
            "params": {"threadId": self.thread_id, "input": [{"type": "text", "text": prompt}]},
        })

        start_time = time.time()
        deadline = start_time + timeout_s
        hard_timeout = env_float("HARD_TIMEOUT_S", "0")
        if hard_timeout <= 0:
            hard_timeout = max(timeout_s * 3, timeout_s + 300)
        hard_deadline = start_time + hard_timeout
        last_event: Dict[str, Any] = {}

        while True:
            now = time.time()
            if now >= hard_deadline:
                raise TimeoutError(f"{self.role_name}: hard timeout waiting for turn completion")
            if now >= deadline:
                raise TimeoutError(f"{self.role_name}: idle timeout waiting for turn completion")
            try:
                msg = self.inbox.get(timeout=0.2)
            except queue.Empty:
                continue

            self._events_count += 1
            last_event = msg

            method = msg.get("method")
            if method:
                log(f"[{self.role_name}] event: {method}")
            if method and method not in ("thread/tokenUsage/updated", "account/rateLimits/updated", "codex/event/token_count"):
                deadline = time.time() + timeout_s

            if method and method.endswith("/requestApproval"):
                if self._handle_request_approval(msg):
                    continue

            self._handle_event(msg)

            if msg.get("method") == "turn/completed":
                assistant_text = (self._assistant_text or "").strip()
                delta_text = "".join(self._delta_parts).strip()
                full_text = "\n\n".join(self._completed_text_parts).strip()
                return TurnResult(
                    role=self.role_name,
                    request_id=req_id,
                    assistant_text=assistant_text,
                    delta_text=delta_text,
                    full_items_text=full_text,
                    events_count=self._events_count,
                    last_event=last_event,
                )
