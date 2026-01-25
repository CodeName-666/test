#!/usr/bin/env python3
"""
codex_multi_role_orchestrator.py

A clean, extensible baseline orchestrator that uses ONLY OpenAI Codex CLI for ALL roles.
It spins up one persistent `codex app-server` process per role and routes messages through
a configurable role pipeline.

Key design goals:
- Only Codex CLI (no Claude/Gemini).
- Easy to extend: add a role by adding an entry to ROLE_SPECS.
- Data-plane uses JSON objects in messages.
- Control-plane uses codex app-server JSONL events.

Requirements:
- Python 3.10+
- `codex` in PATH
- OPENAI_API_KEY set in env (typical)
"""

from __future__ import annotations

import json, re
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# -----------------------------
# Global switch: full access (auto-approve all approvals)
# -----------------------------

FULL_ACCESS = True

# -----------------------------
# Logging
# -----------------------------

def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def _env_flag(name: str, default: str = "0") -> bool:
    """
    Read a boolean flag from env. Truthy: 1/true/yes/on (case-insensitive).
    """
    val = os.environ.get(name, default).strip().lower()
    return val in ("1", "true", "yes", "on")

# -----------------------------
# Codex path helper
# -----------------------------

def find_codex() -> Optional[str]:
    """
    Find the Codex CLI executable, Windows-friendly (codex or codex.cmd).
    """
    return shutil.which("codex") or shutil.which("codex.cmd")

# -----------------------------
# JSON helpers (data-plane)
# -----------------------------

def extract_first_json_object(text: str) -> dict:
    # remove ```json fences
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "")

    start = text.find("{")
    if start == -1:
        raise ValueError("no '{' found")

    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i+1])

    raise ValueError("no complete JSON object found")

def _item_text(item: dict) -> str:
    # direct text
    t = item.get("text")
    if isinstance(t, str) and t.strip():
        return t

    # content: [{type:"text", text:"..."}]
    content = item.get("content")
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str):
                parts.append(c["text"])
        if parts:
            return "".join(parts)

    # fallback: reasoning summary (optional)
    s = item.get("summary")
    if isinstance(s, str) and s.strip():
        return s

    return ""



def parse_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    obj = extract_first_json_object(text)
    if not isinstance(obj, dict):
        raise ValueError("JSON root must be an object")
    return obj

def normalize_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)

# -----------------------------
# Codex app-server adapter
# -----------------------------

def _safe_json_loads(line: str) -> Optional[Dict[str, Any]]:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None

@dataclass
class TurnResult:
    role: str
    request_id: int
    full_text: str
    assistant_text: str  # "best guess" final assistant output (agentMessage/assistantMessage)
    items: List[Dict[str, Any]]  # collected item/completed items (trimmed)
    events_count: int
    last_event: Dict[str, Any]


@dataclass
class CodexRoleClient:
    """
    OPTION 2: Buffer pro Turn.
    - Startet einen persistenten `codex app-server` Prozess
    - Sammelt pro Turn ALLE textuellen Outputs in einem Buffer
    - Liefert am Ende TurnResult (full_text + assistant_text + items + last_event)

    Tipp:
      - Für Routing/JSON Parsing nimm meistens `assistant_text` (und extrahiere JSON daraus)
      - Für Debug/Transparenz nimm `full_text`
    """
    role_name: str
    model: str = "gpt-5.2-codex"
    auto_approve_file_changes: bool = field(
        default_factory=lambda: _env_flag("CODEX_AUTO_APPROVE_FILE_CHANGES", "1")
    )

    proc: Optional[subprocess.Popen] = None
    inbox: "queue.Queue[Dict[str, Any]]" = field(default_factory=queue.Queue)
    thread_id: Optional[str] = None

    _req_id: int = 100

    # --- Option 2 buffers (reset pro run_turn) ---
    _turn_text_parts: List[str] = field(default_factory=list)
    _assistant_text: Optional[str] = None
    _items: List[Dict[str, Any]] = field(default_factory=list)
    _events_count: int = 0

    def start(self) -> None:
        if self.proc is not None:
            return

        codex = shutil.which("codex") or shutil.which("codex.cmd")
        if not codex:
            raise RuntimeError("codex CLI not found in PATH")

        self.proc = subprocess.Popen(
            [codex, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )

        def reader():
            assert self.proc and self.proc.stdout
            for raw in iter(self.proc.stdout.readline, b""):
                msg = _safe_json_loads(raw.decode("utf-8", errors="replace"))
                if msg is not None:
                    self.inbox.put(msg)

        threading.Thread(target=reader, daemon=True).start()

        # Handshake
        self._send({
            "method": "initialize",
            "id": 0,
            "params": {"clientInfo": {"name": self.role_name, "title": self.role_name, "version": "0.1.0"}},
        })
        self._send({"method": "initialized", "params": {}})

        # Start thread
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
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(data)
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

    # -----------------------------
    # Option 2 helpers
    # -----------------------------

    @staticmethod
    def _item_text(item: dict) -> str:
        """
        Extracts text from different item shapes:
        - item.text
        - item.content = [{type:'text', text:'...'}, ...]
        - item.summary (optional fallback)
        """
        t = item.get("text")
        if isinstance(t, str) and t.strip():
            return t

        content = item.get("content")
        if isinstance(content, list):
            parts: List[str] = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str):
                    parts.append(c["text"])
            if parts:
                return "".join(parts)

        s = item.get("summary")
        if isinstance(s, str) and s.strip():
            return s

        return ""

    @staticmethod
    def _norm_type(t: Optional[str]) -> str:
        return (t or "").replace("_", "").lower()

    def _reset_turn_buffers(self) -> None:
        self._turn_text_parts = []
        self._assistant_text = None
        self._items = []
        self._events_count = 0

    def _collect_item_completed(self, msg: Dict[str, Any]) -> None:
        item = (msg.get("params") or {}).get("item") or {}
        itype_raw = item.get("type")
        itype = self._norm_type(itype_raw)
        txt = self._item_text(item)

        # 1) Save item summary for debugging (trim big fields)
        if isinstance(item, dict):
            trimmed = dict(item)
            # Avoid huge blobs
            if "aggregatedOutput" in trimmed and isinstance(trimmed["aggregatedOutput"], str):
                trimmed["aggregatedOutput"] = trimmed["aggregatedOutput"][:8000] + "…"
            self._items.append(trimmed)

        # 2) Add to full text buffer (labelled)
        if txt:
            self._turn_text_parts.append(f"[{itype_raw}] {txt}")

        # 3) Best "assistant output" capture (prefer agent/assistant messages)
        # Your logs show agentMessage (CamelCase), so we match normalized types.
        if itype in ("agentmessage", "assistantmessage"):
            if txt:
                self._assistant_text = txt  # keep last final-style message

    def _handle_request_approval(self, msg: Dict[str, Any]) -> bool:
        """
        Handle approval requests (e.g., file changes). Returns True if handled.
        """
        method = (msg.get("method") or "").strip()
        if not method.endswith("/requestApproval"):
            return False

        # Full access: auto-approve any approval request.
        if FULL_ACCESS:
            req_id = msg.get("id")
            if req_id is not None:
                self._send({"id": req_id, "result": {"approved": True}})
                log(f"[{self.role_name}] auto-approved approval request (id={req_id}, method={method})")
                return True

        # Default: only auto-approve file changes unless disabled.
        if method == "item/fileChange/requestApproval" and self.auto_approve_file_changes:
            req_id = msg.get("id")
            if req_id is not None:
                self._send({"id": req_id, "result": {"approved": True}})
                log(f"[{self.role_name}] auto-approved file change request (id={req_id})")
                return True

        # If not auto-approved, surface a clear error to avoid hanging.
        raise RuntimeError(
            f"{self.role_name}: approval required for {method}. "
            "Set CODEX_AUTO_APPROVE_FILE_CHANGES=1 to auto-approve."
        )

    # -----------------------------
    # Public API
    # -----------------------------

    def run_turn(self, prompt: str, timeout_s: float = 180.0) -> TurnResult:
        """
        Runs one turn and returns a TurnResult with full buffered output.
        """
        self.start()
        assert self.thread_id

        self._req_id += 1
        rid = self._req_id
        self._reset_turn_buffers()

        self._send({
            "method": "turn/start",
            "id": rid,
            "params": {
                "threadId": self.thread_id,
                "input": [{"type": "text", "text": prompt}],
            },
        })

        deadline = time.time() + timeout_s
        last_event: Dict[str, Any] = {}

        while time.time() < deadline:
            try:
                msg = self.inbox.get(timeout=0.2)
            except queue.Empty:
                continue

            self._events_count += 1
            last_event = msg
            method = msg.get("method")

            # Handle approval requests to avoid hanging turns.
            if method and method.endswith("/requestApproval"):
                if self._handle_request_approval(msg):
                    continue

            if method == "item/completed":
                self._collect_item_completed(msg)

            if method == "turn/completed":
                full_text = "\n\n".join(self._turn_text_parts).strip()
                assistant_text = (self._assistant_text or "").strip()

                # Helpful warning if nothing captured
                if not assistant_text and not full_text:
                    log(f"[{self.role_name}] WARNING: turn completed but no text captured at all.")

                return TurnResult(
                    role=self.role_name,
                    request_id=rid,
                    full_text=full_text,
                    assistant_text=assistant_text,
                    items=self._items,
                    events_count=self._events_count,
                    last_event=last_event,
                )

        raise TimeoutError(f"{self.role_name}: timed out waiting for turn completion")


# -----------------------------
# Role specs (easy to extend)
# -----------------------------

@dataclass
class RoleSpec:
    """
    Add new roles by adding entries to ROLE_SPECS.

    - name: role identifier
    - model: codex model string
    - system_instructions: role behavior contract
    """
    name: str
    model: str
    system_instructions: str

# Default pipeline: Planner -> Architect -> Implementer -> Integrator -> Planner (review)
ROLE_SPECS: List[RoleSpec] = [
    RoleSpec(
        name="planner",
        model=os.environ.get("PLANNER_MODEL", "gpt-5.2-codex"),
        system_instructions=(
            "Du bist PLANNER. Zerlege das Ziel in klare Schritte und delegiere Aufgaben an die nächsten Rollen. "
            "Halte es knapp, konkret und priorisiert."
        ),
    ),
    RoleSpec(
        name="architect",
        model=os.environ.get("ARCHITECT_MODEL", "gpt-5.2-codex"),
        system_instructions=(
            "Du bist ARCHITECT. Entwirf die Architektur: Module, Schnittstellen, Datenflüsse, Ordnerstruktur. "
            "Erzeuge umsetzbare Tasks für den Implementierer."
        ),
    ),
    RoleSpec(
        name="implementer",
        model=os.environ.get("IMPLEMENTER_MODEL", "gpt-5.2-codex"),
        system_instructions=(
            "Du bist IMPLEMENTER. Setze die Architektur/Tasks um. Liefere konkrete Artefakte (Code/Dateien/Tests) "
            "als strukturierte Änderungen."
        ),
    ),
    RoleSpec(
        name="integrator",
        model=os.environ.get("INTEGRATOR_MODEL", "gpt-5.2-codex"),
        system_instructions=(
            "Du bist INTEGRATOR/VERIFIER. Prüfe Konsistenz, Integrationsrisiken, Teststrategie, offene Punkte. "
            "Erstelle eine Checkliste und eine Entscheidung: DONE oder CONTINUE."
        ),
    ),
]

# -----------------------------
# Orchestrator (pipeline runner)
# -----------------------------

@dataclass
class OrchestratorConfig:
    goal: str
    cycles: int = 2  # how many times to run the full pipeline (until DONE)
    repair_attempts: int = 1

def json_instruction() -> str:
    return (
        "\n\nWICHTIG: Antworte mit genau einem gültigen JSON-Objekt.\n"
        "Kein Markdown, keine Erklärungen, kein Text außerhalb des JSON.\n"
    )

def ensure_single_json_object(
    client: CodexRoleClient,
    prompt: str,
    attempts: int,
    timeout_s: float,
) -> Dict[str, Any]:
    """
    Runs a turn, ensures a valid JSON object exists; if missing, asks to repair.
    Returns the parsed JSON object.
    """
    last_text = ""
    for n in range(attempts + 1):
        result = client.run_turn(prompt, timeout_s=timeout_s)
        text = (result.assistant_text or result.full_text).strip()
        last_text = text or result.full_text
        try:
            return parse_json_object(text)
        except Exception:
            pass

        if n < attempts:
            prompt = (
                "Deine letzte Antwort war KEIN gültiges JSON-Objekt.\n"
                "Bitte liefere GENAU EIN JSON-Objekt und sonst nichts.\n"
                + json_instruction()
            )

    raise RuntimeError(f"{client.role_name}: no valid JSON object produced. Last text:\n{last_text[:4000]}")

class CodexPipelineOrchestrator:
    def __init__(self, role_specs: List[RoleSpec], cfg: OrchestratorConfig):
        self.cfg = cfg

        self.clients: Dict[str, CodexRoleClient] = {
            rs.name: CodexRoleClient(role_name=rs.name, model=rs.model)
            for rs in role_specs
        }
        self.specs: Dict[str, RoleSpec] = {rs.name: rs for rs in role_specs}
        self.pipeline: List[str] = [rs.name for rs in role_specs]

        # shared memory (JSON objects)
        self.memory: Dict[str, Any] = {
            "goal": cfg.goal,
            "latest_json_by_role": {},  # role -> JSON object
            "history": [],              # list of {role, json_preview}
        }

    def start_all(self) -> None:
        for name in self.pipeline:
            self.clients[name].start()

    def stop_all(self) -> None:
        for c in self.clients.values():
            try:
                c.stop()
            except Exception:
                pass

    def _build_prompt(self, role: str, incoming_json: Optional[Dict[str, Any]]) -> str:
        spec = self.specs[role]
        base = (
            f"Rolle: {role}\n"
            f"{spec.system_instructions}\n\n"
            f"Ziel:\n{self.cfg.goal}\n"
        )

        if incoming_json:
            base += "\nInput von vorheriger Rolle (JSON):\n" + normalize_json(incoming_json) + "\n"

        base += json_instruction()

        # A tiny contract suggestion for each role’s JSON:
        base += (
            "\nHinweis für JSON-Struktur (Empfehlung):\n"
            "- planner: steps/tasks/next_owner\n"
            "- architect: modules/apis/files/tasks\n"
            "- implementer: changes/files/tests/commands\n"
            "- integrator: checklist/issues/status(DONE|CONTINUE)/next_owner\n"
        )
        return base

    def run(self) -> None:
        self.start_all()
        log("All roles started.")

        # Kickoff: planner has no incoming JSON
        incoming: Optional[Dict[str, Any]] = None

        try:
            for cycle in range(1, self.cfg.cycles + 1):
                log(f"=== Cycle {cycle}/{self.cfg.cycles} ===")

                for idx, role in enumerate(self.pipeline):
                    client = self.clients[role]
                    prompt = self._build_prompt(role, incoming)

                    payload = ensure_single_json_object(
                        client=client,
                        prompt=prompt,
                        attempts=self.cfg.repair_attempts,
                        timeout_s=240.0 if role in ("implementer", "integrator") else 180.0,
                    )
                    self.memory["latest_json_by_role"][role] = payload
                    self._remember(role, payload)
                    incoming = payload

                    # Early stop if integrator says DONE (simple heuristic)
                    if role == "integrator":
                        if self._is_done(self.memory["latest_json_by_role"].get("integrator") or {}):
                            log("Integrator indicates DONE. Stopping.")
                            return

                # After finishing pipeline, feed last output back into planner next cycle
                # (incoming already set to last role’s JSON)

        finally:
            self.stop_all()

    def _is_done(self, payload: Dict[str, Any]) -> bool:
        for key in ("status", "decision", "state"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip().upper() == "DONE":
                return True
        for val in payload.values():
            if isinstance(val, str) and val.strip().upper() == "DONE":
                return True
        return False

    def _remember(self, role: str, payload: Dict[str, Any]) -> None:
        preview = normalize_json(payload).strip().replace("\n", " ")
        preview = preview[:500] + ("..." if len(preview) > 500 else "")
        self.memory["history"].append({"role": role, "preview": preview})
        log(f"{role}: JSON {preview}")


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    # Windows console friendliness
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not find_codex():
        raise SystemExit("codex CLI not found in PATH")

    if not os.environ.get("OPENAI_API_KEY"):
        log("WARN: OPENAI_API_KEY is not set. Codex CLI typically needs it.")

    goal = os.environ.get(
        "GOAL",
        "Baue eine kleine Python CLI für TODOs (add/list/done) mit Speicherung in JSON-Datei und Unit-Tests.",
    )

    cfg = OrchestratorConfig(
        goal=goal,
        cycles=int(os.environ.get("CYCLES", "2")),
        repair_attempts=int(os.environ.get("REPAIR_ATTEMPTS", "1")),
    )

    orch = CodexPipelineOrchestrator(ROLE_SPECS, cfg)

    log("Starting Codex-only multi-role orchestrator...")
    log(f"Goal: {goal}")
    log(f"Roles: {', '.join(orch.pipeline)}")
    log("Customize via env: GOAL, CYCLES, REPAIR_ATTEMPTS, *_MODEL")
    log("Stop with Ctrl+C.\n")

    try:
        orch.run()
    except KeyboardInterrupt:
        log("Interrupted.")
    finally:
        orch.stop_all()
        log("Done.")

if __name__ == "__main__":
    main()
