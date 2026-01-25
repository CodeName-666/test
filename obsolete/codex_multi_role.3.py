#!/usr/bin/env python3
"""
codex_multi_role_orchestrator.py (updated)

Changes vs your current code:
- Adds .runs/<run_id>/... artifact storage
- Agents (architect/implementer/integrator) put deep analysis in JSON field: "analysis_md"
- Controller writes analysis_md into markdown file and forwards only reduced JSON (with analysis_md_path)
- Planner + 3 agents only exchange reduced JSON to keep context small
- Still Codex-only, easy to extend via ROLE_SPECS

Environment:
  OPENAI_API_KEY required (typical)
  GOAL="..." optional
  CYCLES=2 optional
  REPAIR_ATTEMPTS=1 optional

Optional approvals:
  FULL_ACCESS=True auto-approves all /requestApproval
  CODEX_AUTO_APPROVE_FILE_CHANGES=1 (default) approves file change requests if not FULL_ACCESS
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
    val = os.environ.get(name, default).strip().lower()
    return val in ("1", "true", "yes", "on")


# -----------------------------
# Codex path helper
# -----------------------------
def find_codex() -> Optional[str]:
    return shutil.which("codex") or shutil.which("codex.cmd")


# -----------------------------
# JSON helpers (data-plane)
# -----------------------------
def extract_first_json_object(text: str) -> dict:
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


def parse_json_object(text: str) -> Dict[str, Any]:
    obj = extract_first_json_object((text or "").strip())
    if not isinstance(obj, dict):
        raise ValueError("JSON root must be an object")
    return obj


def normalize_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# -----------------------------
# Codex app-server adapter
# -----------------------------
def _safe_json_loads(line: str) -> Optional[Dict[str, Any]]:
    line = (line or "").strip()
    if not line:
        return None
    try:
        return json.loads(line)  # event stream is JSONL; do NOT use extract_first_json_object here
    except json.JSONDecodeError:
        return None


@dataclass
class TurnResult:
    role: str
    request_id: int
    full_text: str
    assistant_text: str
    items: List[Dict[str, Any]]
    events_count: int
    last_event: Dict[str, Any]


@dataclass
class CodexRoleClient:
    """
    OPTION 2: Buffer per turn (best practical logging).
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

    _turn_text_parts: List[str] = field(default_factory=list)
    _assistant_text: Optional[str] = None
    _items: List[Dict[str, Any]] = field(default_factory=list)
    _events_count: int = 0

    def start(self) -> None:
        if self.proc is not None:
            return

        codex = find_codex()
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

    @staticmethod
    def _norm_type(t: Optional[str]) -> str:
        return (t or "").replace("_", "").lower()

    @staticmethod
    def _item_text(item: dict) -> str:
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

        if isinstance(item, dict):
            trimmed = dict(item)
            if "aggregatedOutput" in trimmed and isinstance(trimmed["aggregatedOutput"], str):
                trimmed["aggregatedOutput"] = trimmed["aggregatedOutput"][:8000] + "…"
            self._items.append(trimmed)

        if txt:
            self._turn_text_parts.append(f"[{itype_raw}] {txt}")

        if itype in ("agentmessage", "assistantmessage"):
            if txt:
                self._assistant_text = txt  # last final-style message wins

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
        rid = self._req_id
        self._reset_turn_buffers()

        self._send({
            "method": "turn/start",
            "id": rid,
            "params": {"threadId": self.thread_id, "input": [{"type": "text", "text": prompt}]},
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

            if method and method.endswith("/requestApproval"):
                if self._handle_request_approval(msg):
                    continue

            if method == "item/completed":
                self._collect_item_completed(msg)

            if method == "turn/completed":
                full_text = "\n\n".join(self._turn_text_parts).strip()
                assistant_text = (self._assistant_text or "").strip()
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
    name: str
    model: str
    system_instructions: str


ROLE_SPECS: List[RoleSpec] = [
    RoleSpec(
        name="planner",
        model=os.environ.get("PLANNER_MODEL", "gpt-5.1-codex-mini"),
        system_instructions=(
            "Du bist PLANNER. Du koordinierst fachlich: Schritte planen, priorisieren, delegieren. "
            "Du gibst next_owner zurück. Keine Tools/Commands. Keine Repo-Suche."
        ),
    ),
    RoleSpec(
        name="architect",
        model=os.environ.get("ARCHITECT_MODEL", "gpt-5.1-codex-mini"),
        system_instructions=(
            "Du bist ARCHITECT. Tiefe Analyse gehört in analysis_md (Markdown String im JSON). "
            "Dein Handoff-JSON an den Planner soll klein sein."
        ),
    ),
    RoleSpec(
        name="implementer",
        model=os.environ.get("IMPLEMENTER_MODEL", "gpt-5.1-codex-mini"),
        system_instructions=(
            "Du bist IMPLEMENTER. Tiefe Analyse/Details in analysis_md (Markdown). "
            "Im JSON nur kurze Summary + konkrete next steps / change plan. Klein halten."
        ),
    ),
    RoleSpec(
        name="integrator",
        model=os.environ.get("INTEGRATOR_MODEL", "gpt-5.1-codex-mini"),
        system_instructions=(
            "Du bist INTEGRATOR/VERIFIER. Tiefe Analyse in analysis_md (Markdown). "
            "Gib status DONE|CONTINUE + next_owner zurück."
        ),
    ),
]


# -----------------------------
# Orchestrator
# -----------------------------
@dataclass
class OrchestratorConfig:
    goal: str
    cycles: int = 2
    repair_attempts: int = 1


def json_instruction() -> str:
    return (
        "\n\nFORMAT (streng):\n"
        "- Antworte mit genau EINEM gültigen JSON-Objekt.\n"
        "- Kein Text außerhalb des JSON. Kein Markdown-Codefence.\n"
        "- Wenn unklar: gib JSON mit Feld \"error\" zurück.\n"
    )


def role_schema_hint(role: str) -> str:
    if role == "planner":
        return (
            "\nJSON-SCHEMA (planner, klein):\n"
            "{\n"
            "  \"summary\": \"kurz\",\n"
            "  \"tasks\": [{\"id\":\"T1\",\"title\":\"...\",\"owner\":\"architect|implementer|integrator\",\"priority\":1}],\n"
            "  \"next_owner\": \"architect|implementer|integrator\",\n"
            "  \"notes\": \"kurz\"\n"
            "}\n"
        )
    return (
        f"\nJSON-SCHEMA ({role}, klein + markdown):\n"
        "{\n"
        "  \"summary\": \"max 5 Zeilen\",\n"
        "  \"key_points\": [\"...\"],\n"
        "  \"requests\": {\"need_more_context\": false, \"files\": [], \"why\": \"\"},\n"
        "  \"analysis_md\": \"(Markdown, kann lang sein)\",\n"
        "  \"analysis_md_path\": \"(setzt der Controller)\",\n"
        "  \"next_owner_suggestion\": \"planner\",\n"
        "  \"status\": \"(optional, z.B. DONE|CONTINUE)\"\n"
        "}\n"
    )


def ensure_single_json_object(
    client: CodexRoleClient,
    prompt: str,
    attempts: int,
    timeout_s: float,
) -> Tuple[TurnResult, Dict[str, Any]]:
    """
    Run once (collect TurnResult), parse JSON from assistant_text/full_text.
    Repair retry re-runs the turn.
    """
    last_text = ""
    last_turn: Optional[TurnResult] = None

    for n in range(attempts + 1):
        tr = client.run_turn(prompt, timeout_s=timeout_s)
        last_turn = tr
        text = (tr.assistant_text or tr.full_text).strip()
        last_text = text or tr.full_text
        try:
            return tr, parse_json_object(text)
        except Exception:
            pass

        if n < attempts:
            prompt = (
                "Deine letzte Antwort war KEIN gültiges JSON-Objekt.\n"
                "Bitte liefere GENAU EIN JSON-Objekt und sonst nichts.\n"
                + json_instruction()
            )

    raise RuntimeError(f"{client.role_name}: no valid JSON object produced. Last text:\n{last_text[:4000]}")


class CodexRunsPipelineOrchestrator:
    """
    Controller:
    - runs roles in pipeline order
    - stores deep analysis markdown into .runs/<run_id>/...
    - forwards only reduced JSON between roles
    """
    def __init__(self, role_specs: List[RoleSpec], cfg: OrchestratorConfig):
        self.cfg = cfg
        self.pipeline: List[str] = [rs.name for rs in role_specs]
        self.specs: Dict[str, RoleSpec] = {rs.name: rs for rs in role_specs}
        self.clients: Dict[str, CodexRoleClient] = {
            rs.name: CodexRoleClient(role_name=rs.name, model=rs.model) for rs in role_specs
        }

        self.run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self.runs_dir = Path(".runs") / self.run_id
        self.runs_dir.mkdir(parents=True, exist_ok=True)

        self.memory: Dict[str, Any] = {
            "goal": cfg.goal,
            "latest_json_by_role": {},
            "history": [],
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

    def _write_text(self, rel_path: str, content: str) -> str:
        p = self.runs_dir / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content or "", encoding="utf-8")
        return str(p)

    def _reduce_and_store(self, role: str, turn: TurnResult, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        - Always store full output for debugging
        - If payload has analysis_md (string), store it to analysis.md and remove it from JSON
        - Add analysis_md_path to reduced JSON
        - Store reduced JSON as handoff.json
        """
        role_dir = f"{role}/turn_{turn.request_id}"

        # store full output (debug)
        self._write_text(f"{role_dir}/full_output.md", turn.full_text or turn.assistant_text or "")

        reduced = dict(payload)

        analysis_md = reduced.pop("analysis_md", None)
        if isinstance(analysis_md, str) and analysis_md.strip():
            md_path = self._write_text(f"{role_dir}/analysis.md", analysis_md.strip() + "\n")
            reduced["analysis_md_path"] = md_path
        else:
            # ensure path is still present, even if agent didn't send analysis_md
            reduced["analysis_md_path"] = str(self.runs_dir / f"{role_dir}/full_output.md")

        self._write_text(f"{role_dir}/handoff.json", normalize_json(reduced))
        return reduced

    def _build_prompt(self, role: str, incoming_json: Optional[Dict[str, Any]]) -> str:
        spec = self.specs[role]
        base = (
            f"Rolle: {role}\n"
            f"{spec.system_instructions}\n\n"
            f"Ziel:\n{self.cfg.goal}\n"
        )

        if incoming_json:
            base += "\nInput (reduziertes JSON):\n" + normalize_json(incoming_json) + "\n"

        base += json_instruction()
        base += role_schema_hint(role)

        # hard guard against “agent mode” chatter
        base += (
            "\nREGELN:\n"
            "- KEIN Repo-Scan/keine AGENTS.md Suche.\n"
            "- KEINE Tools/Commands ausführen.\n"
            "- Tiefe Analyse nur im Feld analysis_md (Markdown) innerhalb des JSON.\n"
            "- Das Handoff-JSON muss klein bleiben.\n"
        )
        return base

    def _is_done(self, payload: Dict[str, Any]) -> bool:
        v = payload.get("status")
        return isinstance(v, str) and v.strip().upper() == "DONE"

    def run(self) -> None:
        self.start_all()
        log(f"Run folder: {self.runs_dir}")

        incoming: Optional[Dict[str, Any]] = None

        try:
            for cycle in range(1, self.cfg.cycles + 1):
                log(f"=== Cycle {cycle}/{self.cfg.cycles} ===")

                for role in self.pipeline:
                    prompt = self._build_prompt(role, incoming)

                    timeout = 240.0 if role in ("implementer", "integrator", "architect") else 180.0
                    turn, payload = ensure_single_json_object(
                        client=self.clients[role],
                        prompt=prompt,
                        attempts=self.cfg.repair_attempts,
                        timeout_s=timeout,
                    )

                    reduced = self._reduce_and_store(role, turn, payload)

                    self.memory["latest_json_by_role"][role] = reduced
                    self.memory["history"].append({"role": role, "turn": turn.request_id, "handoff": reduced})

                    # forward only reduced JSON
                    incoming = reduced

                    # stop condition
                    if role == "integrator" and self._is_done(reduced):
                        log("Integrator indicates DONE. Stopping.")
                        return

        finally:
            # store controller state summary
            (self.runs_dir / "controller_state.json").write_text(
                json.dumps(self.memory, ensure_ascii=False, indent=2),
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

    orch = CodexRunsPipelineOrchestrator(ROLE_SPECS, cfg)

    log("Starting Codex multi-role orchestrator (reduced JSON + .runs markdown storage)...")
    log(f"Goal: {goal}")
    log(f"Roles: {', '.join(orch.pipeline)}")
    log("Artifacts: .runs/<run_id>/role/turn_<id>/")
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
