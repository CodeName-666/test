#!/usr/bin/env python3
"""
codex_runs_orchestrator_v2.py

NEU (Fix A+B+C) + OPTION 1 (RAW EVENT LOGGING)

A) Anti-Prompt-Parse Fix:
   - JSON wird NUR aus assistant_text geparst (niemals aus full_text/prompt).
   - Wenn assistant_text leer ist -> Turn gilt als fehlgeschlagen + Repair/Retry.
   - Prompt-Schema-Beispiele sind absichtlich NICHT gültiges JSON.

B) Output Capture Fix:
   - Erfasst Assistant-Output robuster:
     * item/delta (text chunks)
     * item/completed (final items)
     * normalisiert Typen (agentMessage/assistantMessage/agent_message/...)
   - Wenn nach turn/completed kein assistant_text vorhanden -> Fail.

C) Code schreiben:
   - Implementer liefert ein JSON mit "files": [{ "path": "...", "content": "..." }, ...]
   - Orchestrator schreibt diese Dateien ins Repo (und loggt in .runs).
   - Tests optional via RUN_TESTS=1 (pytest) (Controller, nicht Agent).

OPTION 1 Logging:
   - Loggt JEDE Event-Zeile (RAW EVENT) pro Rolle in .runs/.../events.jsonl
   - Zusätzlich loggt es kurz auf stdout.

Rollen:
  planner + architect + implementer + integrator
  (alle Codex CLI app-server, je Rolle ein Prozess)

ENV:
  OPENAI_API_KEY               (typisch nötig)
  GOAL="..."                   (optional)
  CYCLES=2                     (default 2)
  REPAIR_ATTEMPTS=1            (default 1)
  RUN_TESTS=0|1                (default 0)
  PYTEST_CMD="python -m pytest" (default)
  PLANNER_TIMEOUT_S            (default 240)
  ROLE_TIMEOUT_S               (default 600)
  HARD_TIMEOUT_S               (default 0 -> auto based on timeout)
  CODEX_ALLOW_COMMANDS         (default 1)
  CODEX_AUTO_APPROVE_COMMANDS  (default 0)
  CODEX_AUTO_APPROVE_FILE_CHANGES (default 1)
  DEFAULT_MODEL                (default gpt-5.2-codex)
  *_MODEL optional pro Rolle (z.B. PLANNER_MODEL, ...)
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


# -----------------------------
# Logging
# -----------------------------

def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# -----------------------------
# Approvals
# -----------------------------

# Set FULL_ACCESS=True to auto-approve all approval requests from codex app-server.
FULL_ACCESS = False


# -----------------------------
# Helpers / ENV
# -----------------------------

def _env_int(name: str, default: str) -> int:
    try:
        return int(os.environ.get(name, default).strip())
    except Exception:
        return int(default)

def _env_float(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default).strip())
    except Exception:
        return float(default)

def _env_flag(name: str, default: str = "0") -> bool:
    val = os.environ.get(name, default).strip().lower()
    return val in ("1", "true", "yes", "on")

def find_codex() -> Optional[str]:
    return shutil.which("codex") or shutil.which("codex.cmd")


# -----------------------------
# JSON extraction (ONLY for assistant_text)
# -----------------------------

def extract_first_json_object(text: str) -> dict:
    """
    Extract the first complete {...} JSON object from assistant_text.
    Allows extra prefix/suffix text (but we try to prevent that via prompts + repair).
    """
    text = (text or "").strip()
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
                return json.loads(text[start : i + 1])

    raise ValueError("no complete JSON object found")

def parse_json_object_from_assistant_text(assistant_text: str) -> Dict[str, Any]:
    obj = extract_first_json_object(assistant_text)
    if not isinstance(obj, dict):
        raise ValueError("JSON root must be an object")
    return obj

def normalize_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# -----------------------------
# Codex app-server: event parsing
# -----------------------------

def _safe_event_json(line: str) -> Optional[Dict[str, Any]]:
    """
    codex app-server outputs JSONL (one JSON object per line).
    DO NOT use extract_first_json_object here.
    """
    line = (line or "").strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None

def _norm_type(t: Optional[str]) -> str:
    return (t or "").replace("_", "").lower()

def _extract_text_from_item_like(obj: dict) -> str:
    """
    Extract text from:
      - {text: "..."}
      - {content: [{type:"text", text:"..."}]}
      - {summary: "..."} (fallback)
    """
    t = obj.get("text")
    if isinstance(t, str) and t.strip():
        return t

    content = obj.get("content")
    if isinstance(content, list):
        parts: List[str] = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str):
                parts.append(c["text"])
        if parts:
            return "".join(parts)

    s = obj.get("summary")
    if isinstance(s, str) and s.strip():
        return s

    return ""


@dataclass
class TurnResult:
    role: str
    request_id: int
    assistant_text: str        # final-ish assistant output (must exist to parse JSON)
    delta_text: str            # streamed chunks concatenated (debug)
    full_items_text: str       # all item/completed texts labeled (debug)
    events_count: int
    last_event: Dict[str, Any]


@dataclass
class CodexRoleClient:
    role_name: str
    model: str = "gpt-5.2-codex"
    reasoning_effort: Optional[str] = None
    auto_approve_file_changes: bool = field(
        default_factory=lambda: _env_flag("CODEX_AUTO_APPROVE_FILE_CHANGES", "1")
    )
    allow_commands: bool = field(default_factory=lambda: _env_flag("CODEX_ALLOW_COMMANDS", "1"))
    auto_approve_commands: bool = field(default_factory=lambda: _env_flag("CODEX_AUTO_APPROVE_COMMANDS", "0"))

    proc: Optional[subprocess.Popen] = None
    inbox: "queue.Queue[Dict[str, Any]]" = field(default_factory=queue.Queue)
    thread_id: Optional[str] = None
    _req_id: int = 100

    # option1 logging sink
    events_file: Optional[Path] = None

    # per-turn buffers
    _delta_parts: List[str] = field(default_factory=list)
    _completed_text_parts: List[str] = field(default_factory=list)
    _assistant_text: Optional[str] = None
    _events_count: int = 0

    def start(self) -> None:
        if self.proc is not None:
            return

        codex = find_codex()
        if not codex:
            raise RuntimeError("codex CLI not found in PATH")

        # Put -c before the subcommand so codex CLI applies the override.
        cmd = [codex]
        if self.reasoning_effort:
            # Override per-process config so we don't rely on global ~/.codex/config.toml
            cmd += ["-c", f"model_reasoning_effort={json.dumps(self.reasoning_effort)}"]
        cmd += ["app-server"]

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )

        def reader():
            assert self.proc and self.proc.stdout
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                msg = _safe_event_json(line)
                if msg is None:
                    continue
                # OPTION 1: raw event logging (jsonl)
                if self.events_file:
                    try:
                        with self.events_file.open("a", encoding="utf-8") as f:
                            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                self.inbox.put(msg)

        threading.Thread(target=reader, daemon=True).start()

        # handshake
        self._send({
            "method": "initialize",
            "id": 0,
            "params": {"clientInfo": {"name": self.role_name, "title": self.role_name, "version": "0.1.0"}},
        })
        self._send({"method": "initialized", "params": {}})

        # start thread
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

    def _reset_turn_buffers(self) -> None:
        self._delta_parts = []
        self._completed_text_parts = []
        self._assistant_text = None
        self._events_count = 0

    def _handle_event(self, msg: Dict[str, Any]) -> None:
        """
        B) Robust capture:
          - item/delta: collect delta text
          - item/completed: collect labeled text, capture final assistant_text from agent/assistant messages
        """
        method = msg.get("method") or ""

        if method == "item/delta":
            # shapes vary: some use params.delta or params.item
            params = msg.get("params") or {}
            delta = params.get("delta") or params.get("item") or {}
            if isinstance(delta, dict):
                txt = _extract_text_from_item_like(delta)
                if txt:
                    self._delta_parts.append(txt)

        if method == "item/completed":
            item = (msg.get("params") or {}).get("item") or {}
            if not isinstance(item, dict):
                return
            itype_raw = item.get("type")
            itype = _norm_type(itype_raw)
            txt = _extract_text_from_item_like(item)
            if txt:
                self._completed_text_parts.append(f"[{itype_raw}] {txt}")

            # capture final-style assistant outputs
            if itype in ("agentmessage", "assistantmessage"):
                if txt:
                    self._assistant_text = txt

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

        # Command execution approvals.
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
            "Set FULL_ACCESS=True or CODEX_AUTO_APPROVE_FILE_CHANGES=1."
        )

    def run_turn(self, prompt: str, timeout_s: float = 180.0) -> TurnResult:
        self.start()
        assert self.thread_id

        self._req_id += 1
        rid = self._req_id
        self._reset_turn_buffers()

        self._send({
            "method": "turn/start",
            "id": rid,
            "params": {"threadId": self.thread_id, "input": [{"type": "text", "text": prompt}]},
        })

        start_time = time.time()
        deadline = start_time + timeout_s
        hard_timeout_s = _env_float("HARD_TIMEOUT_S", "0")
        if hard_timeout_s <= 0:
            hard_timeout_s = max(timeout_s * 3, timeout_s + 300)
        hard_deadline = start_time + hard_timeout_s
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

            # OPTION 1: short console ping
            m = msg.get("method")
            if m:
                log(f"[{self.role_name}] event: {m}")

            # Sliding idle timeout: extend when we see activity.
            if m and m not in ("thread/tokenUsage/updated", "account/rateLimits/updated", "codex/event/token_count"):
                deadline = time.time() + timeout_s

            # Handle approval requests to avoid hanging turns.
            if m and m.endswith("/requestApproval"):
                if self._handle_request_approval(msg):
                    continue

            self._handle_event(msg)

            if msg.get("method") == "turn/completed":
                assistant_text = (self._assistant_text or "").strip()
                delta_text = "".join(self._delta_parts).strip()
                full_items_text = "\n\n".join(self._completed_text_parts).strip()

                return TurnResult(
                    role=self.role_name,
                    request_id=rid,
                    assistant_text=assistant_text,
                    delta_text=delta_text,
                    full_items_text=full_items_text,
                    events_count=self._events_count,
                    last_event=last_event,
                )
 


# -----------------------------
# Roles / Prompts
# -----------------------------

@dataclass
class RoleSpec:
    name: str
    model: str
    reasoning_effort: Optional[str]
    system_instructions: str


DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "gpt-5.1-codex-mini")

ROLE_SPECS: List[RoleSpec] = [
    RoleSpec(
        name="planner",
        model=os.environ.get("PLANNER_MODEL", DEFAULT_MODEL),
        reasoning_effort="high",
        system_instructions=(
            "Du bist PLANNER. Plane und delegiere. Gib next_owner zurück. "
            "Tools/Commands sind erlaubt. Du darfst Dateien NUR LESEN, NICHT schreiben. "
            "Nur JSON, kein Zusatztext."
        ),
    ),
    RoleSpec(
        name="architect",
        model=os.environ.get("ARCHITECT_MODEL", DEFAULT_MODEL),
        reasoning_effort="high",
        system_instructions=(
            "Du bist ARCHITECT. Tools/Commands sind erlaubt. Du darfst Dateien NUR LESEN, "
            "NICHT schreiben. Tiefe Analyse in analysis_md (Markdown String im JSON). "
            "Handoff klein halten."
        ),
    ),
    RoleSpec(
        name="implementer",
        model=os.environ.get("IMPLEMENTER_MODEL", DEFAULT_MODEL),
        reasoning_effort="high",
        system_instructions=(
            "Du bist IMPLEMENTER. Tools/Commands sind erlaubt. Du darfst Dateien NUR LESEN, "
            "NICHT schreiben. Gib Dateiänderungen ausschließlich als Vorschlag im Feld "
            "files=[{path,content}] zurück. "
            "Tiefe Analyse in analysis_md (Markdown). Handoff klein halten."
        ),
    ),
    RoleSpec(
        name="integrator",
        model=os.environ.get("INTEGRATOR_MODEL", DEFAULT_MODEL),
        reasoning_effort="high",
        system_instructions=(
            "Du bist INTEGRATOR/VERIFIER. Tools/Commands sind erlaubt. Du darfst Dateien LESEN "
            "und SCHREIBEN. Prüfe Plan/Änderungen. Gib status DONE|CONTINUE + next_owner zurück. "
            "Tiefe Analyse in analysis_md (Markdown)."
        ),
    ),
]


def json_contract_instruction() -> str:
    return (
        "\nFORMAT-VERTRAG (streng):\n"
        "- Antworte mit GENAU EINEM gültigen JSON-Objekt.\n"
        "- KEIN Text außerhalb des JSON. KEIN Markdown-Codefence.\n"
        "- Wenn unklar: gib JSON mit Feld \"error\" zurück.\n"
    )


def schema_hint_non_json(role: str) -> str:
    """
    ABSICHTLICH NICHT-JSON (Fix A), damit niemals Prompt-Text als Antwort geparst werden kann.
    """
    if role == "planner":
        return (
            "\nSCHEMA-HINWEIS (planner, PSEUDO):\n"
            "summary: <string>\n"
            "tasks: [ { id: <string>, title: <string>, owner: architect|implementer|integrator, priority: <int> } ]\n"
            "next_owner: architect|implementer|integrator\n"
            "notes: <string>\n"
        )
    if role == "implementer":
        return (
            "\nSCHEMA-HINWEIS (implementer, PSEUDO):\n"
            "summary: <string>\n"
            "files: [ { path: <string>, content: <string> } ]\n"
            "analysis_md: <markdown>\n"
            "analysis_md_path: <string>  # setzt Controller\n"
            "next_owner_suggestion: planner\n"
        )
    return (
        f"\nSCHEMA-HINWEIS ({role}, PSEUDO):\n"
        "summary: <string>\n"
        "key_points: [<string>]\n"
        "requests: { need_more_context: <bool>, files: [<string>], why: <string> }\n"
        "analysis_md: <markdown>\n"
        "analysis_md_path: <string>  # setzt Controller\n"
        "status: <DONE|CONTINUE?>\n"
        "next_owner_suggestion: planner\n"
    )


# -----------------------------
# Orchestrator (Controller)
# -----------------------------

@dataclass
class OrchestratorConfig:
    goal: str
    cycles: int = 2
    repair_attempts: int = 1
    run_tests: bool = False
    pytest_cmd: str = "python -m pytest"


class CodexRunsOrchestratorV2:
    def __init__(self, role_specs: List[RoleSpec], cfg: OrchestratorConfig):
        self.cfg = cfg
        self.pipeline = [rs.name for rs in role_specs]
        self.specs = {rs.name: rs for rs in role_specs}

        self.run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self.runs_dir = Path(".runs") / self.run_id
        self.runs_dir.mkdir(parents=True, exist_ok=True)

        self.clients: Dict[str, CodexRoleClient] = {}
        for rs in role_specs:
            c = CodexRoleClient(role_name=rs.name, model=rs.model, reasoning_effort=rs.reasoning_effort)
            # option1 raw event log file per role
            role_events = self.runs_dir / rs.name / "events.jsonl"
            role_events.parent.mkdir(parents=True, exist_ok=True)
            c.events_file = role_events
            self.clients[rs.name] = c

        self.state: Dict[str, Any] = {
            "goal": cfg.goal,
            "latest_json_by_role": {},
            "history": [],
        }

    def start_all(self) -> None:
        for r in self.pipeline:
            self.clients[r].start()

    def stop_all(self) -> None:
        for c in self.clients.values():
            try:
                c.stop()
            except Exception:
                pass

    def _write_text(self, rel: str, content: str) -> str:
        p = self.runs_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content or "", encoding="utf-8")
        return str(p)

    def _build_prompt(self, role: str, incoming_reduced_json: Optional[Dict[str, Any]]) -> str:
        spec = self.specs[role]
        base = (
            f"Rolle: {role}\n"
            f"{spec.system_instructions}\n\n"
            f"Ziel:\n{self.cfg.goal}\n"
        )
        if incoming_reduced_json:
            base += "\nInput (reduziertes JSON, klein halten):\n" + normalize_json(incoming_reduced_json) + "\n"

        base += json_contract_instruction()
        base += schema_hint_non_json(role)
        base += (
            "\nREGELN:\n"
            "- Tools/Commands sind erlaubt.\n"
            "- Planner/Architect/Implementer dürfen NUR lesen (keine Dateiänderungen).\n"
            "- Integrator darf lesen UND schreiben.\n"
            "- Tiefe Analyse NUR im Feld analysis_md (Markdown String im JSON).\n"
            "- Ausgabe JSON klein halten (analysis_md darf lang sein, wird ausgelagert).\n"
        )
        return base

    # --------- FIX A: parse only assistant_text; never full_text/prompt ----------
    def _run_and_parse_json_strict(self, role: str, prompt: str, timeout_s: float) -> Tuple[TurnResult, Dict[str, Any]]:
        last_assistant_text = ""
        last_turn: Optional[TurnResult] = None

        for attempt in range(self.cfg.repair_attempts + 1):
            tr = self.clients[role].run_turn(prompt, timeout_s=timeout_s)
            last_turn = tr
            last_assistant_text = (tr.assistant_text or "").strip()

            # Store per-turn raw captures (debug)
            turn_dir = f"{role}/turn_{tr.request_id}"
            self._write_text(f"{turn_dir}/assistant_text.txt", tr.assistant_text or "")
            self._write_text(f"{turn_dir}/delta_text.txt", tr.delta_text or "")
            self._write_text(f"{turn_dir}/items_text.md", tr.full_items_text or "")
            self._write_text(f"{turn_dir}/prompt.txt", prompt)

            # If assistant_text missing -> FAIL/RETRY (Fix A+B)
            if not last_assistant_text:
                if attempt < self.cfg.repair_attempts:
                    prompt = (
                        "Deine letzte Antwort konnte nicht als Assistant-Text erfasst werden.\n"
                        "Bitte liefere GENAU EIN JSON-Objekt und sonst nichts.\n"
                        + json_contract_instruction()
                    )
                    continue
                raise RuntimeError(f"{role}: assistant_text missing after turn completion; refusing to parse prompt/full_text.")

            # Parse JSON only from assistant_text
            try:
                payload = parse_json_object_from_assistant_text(last_assistant_text)
                return tr, payload
            except Exception:
                if attempt < self.cfg.repair_attempts:
                    prompt = (
                        "Deine letzte Antwort war KEIN gültiges JSON-Objekt.\n"
                        "Bitte liefere GENAU EIN JSON-Objekt und sonst nichts.\n"
                        + json_contract_instruction()
                    )
                    continue
                raise RuntimeError(f"{role}: invalid JSON in assistant_text. First 2000 chars:\n{last_assistant_text[:2000]}")

        # should never get here
        raise RuntimeError(f"{role}: failed to get valid JSON")

    def _reduce_and_store_payload(self, role: str, turn: TurnResult, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Store deep analysis in .runs and forward only reduced JSON.
        """
        turn_dir = f"{role}/turn_{turn.request_id}"
        reduced = dict(payload)

        analysis_md = reduced.pop("analysis_md", None)
        if isinstance(analysis_md, str) and analysis_md.strip():
            md_path = self._write_text(f"{turn_dir}/analysis.md", analysis_md.strip() + "\n")
            reduced["analysis_md_path"] = md_path
        else:
            # ensure path exists anyway
            reduced["analysis_md_path"] = str(self.runs_dir / f"{turn_dir}/items_text.md")

        self._write_text(f"{turn_dir}/handoff.json", normalize_json(reduced))
        return reduced

    # --------- FIX C: write files from implementer JSON ----------
    def _apply_implementer_files(self, reduced_payload: Dict[str, Any], turn_dir: str) -> None:
        files = reduced_payload.get("files")
        if not isinstance(files, list):
            return

        applied: List[Dict[str, Any]] = []
        for f in files:
            if not isinstance(f, dict):
                continue
            path = f.get("path")
            content = f.get("content")
            if not isinstance(path, str) or not path.strip():
                continue
            if not isinstance(content, str):
                continue

            # safety: prevent absolute paths and path traversal
            norm = Path(path)
            if norm.is_absolute() or ".." in norm.parts:
                applied.append({"path": path, "status": "SKIPPED", "reason": "unsafe path"})
                continue

            target = Path(".") / norm
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

            applied.append({"path": path, "status": "WROTE", "bytes": len(content.encode("utf-8"))})

        self._write_text(f"{turn_dir}/applied_files.json", json.dumps(applied, ensure_ascii=False, indent=2))

    def _run_tests_if_enabled(self) -> None:
        if not self.cfg.run_tests:
            return
        cmd = os.environ.get("PYTEST_CMD", self.cfg.pytest_cmd)
        try:
            p = subprocess.run(cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
            out = p.stdout.decode("utf-8", errors="replace")
            self._write_text("tests/pytest_output.txt", out)
            self._write_text("tests/pytest_rc.txt", str(p.returncode))
            log(f"[tests] pytest rc={p.returncode}")
        except Exception as e:
            self._write_text("tests/pytest_error.txt", str(e))
            log(f"[tests] pytest failed to run: {e}")

    def run(self) -> None:
        self.start_all()
        log(f"Run folder: {self.runs_dir}")

        incoming: Optional[Dict[str, Any]] = None
        planner_timeout = _env_float("PLANNER_TIMEOUT_S", "240")
        role_timeout = _env_float("ROLE_TIMEOUT_S", "600")

        try:
            for cycle in range(1, self.cfg.cycles + 1):
                log(f"=== Cycle {cycle}/{self.cfg.cycles} ===")

                for role in self.pipeline:
                    prompt = self._build_prompt(role, incoming)
                    timeout = planner_timeout if role == "planner" else role_timeout

                    turn, payload = self._run_and_parse_json_strict(role, prompt, timeout_s=timeout)
                    reduced = self._reduce_and_store_payload(role, turn, payload)

                    # Apply code changes if implementer
                    if role == "implementer":
                        turn_dir = f"{role}/turn_{turn.request_id}"
                        self._apply_implementer_files(reduced, turn_dir)
                        # optional tests after writing
                        self._run_tests_if_enabled()

                    self.state["latest_json_by_role"][role] = reduced
                    self.state["history"].append({"role": role, "turn": turn.request_id, "handoff": reduced})
                    incoming = reduced  # only reduced JSON forwarded (small context)

                    # stop condition from integrator
                    if role == "integrator":
                        st = reduced.get("status")
                        if isinstance(st, str) and st.strip().upper() == "DONE":
                            log("Integrator indicates DONE. Stopping.")
                            return

        finally:
            (self.runs_dir / "controller_state.json").write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.stop_all()


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if load_dotenv:
        load_dotenv()
    else:
        log("WARN: python-dotenv not installed; .env will not be loaded automatically.")

    if not find_codex():
        raise SystemExit("codex CLI not found in PATH")

    if not os.environ.get("OPENAI_API_KEY"):
        log("WARN: OPENAI_API_KEY is not set. Codex CLI typically needs it.")

    #goal = os.environ.get(
    #    "GOAL",
    #    "Baue eine kleine Python CLI für TODOs (add/list/done) mit Speicherung in JSON-Datei und Unit-Tests.",
    #)

    goal = os.environ.get(
        "GOAL",
        "Implementiere diese codex_multi_role_3_gen.py Datei komplett neu Teile dabei das Skript in seperate Dateien auf. Jede Klasse soll eine einge datei bekommen. Funktionen sollen strukturiert und Übersichtlich aufgebaut sein. ",
    )

    cfg = OrchestratorConfig(
        goal=goal,
        cycles=_env_int("CYCLES", "2"),
        repair_attempts=_env_int("REPAIR_ATTEMPTS", "1"),
        run_tests=_env_flag("RUN_TESTS", "0"),
        pytest_cmd=os.environ.get("PYTEST_CMD", "python -m pytest"),
    )

    orch = CodexRunsOrchestratorV2(ROLE_SPECS, cfg)

    log("Starting Codex orchestrator (Fix A+B+C + Option1 raw event logs)...")
    log(f"Goal: {goal}")
    log(f"Roles: {', '.join(orch.pipeline)}")
    log("Reasoning effort: from ROLE_SPECS")
    log(f"Artifacts: .runs/{orch.run_id}/...")
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
