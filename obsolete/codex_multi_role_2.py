#!/usr/bin/env python3
"""
codex_runs_orchestrator.py

Codex-only multi-agent orchestrator:
- Roles: planner + 3 agents (architect, implementer, integrator)
- Each role runs in its own persistent `codex app-server` process (one thread per role)
- Each role produces a SMALL JSON "handoff" (for the next step / planner)
- Any deep analysis / long content is stored by the CONTROLLER as Markdown under: .runs/<run_id>/
- The JSON each role returns includes a reference path to the markdown written for that turn

Key idea:
- Agent outputs one JSON object. It may include a large "analysis_md" string (markdown).
- Controller writes that markdown into .runs and replaces it with "analysis_md_path"
- Controller forwards only the reduced JSON onward (small context).

Env:
  OPENAI_API_KEY required for codex
  GOAL="..." optional
  CYCLES=2 optional
  *_MODEL optional per role, default "gpt-5.2-codex"

Run:
  python codex_runs_orchestrator.py
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
# Logging
# -----------------------------

def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# -----------------------------
# JSON helpers
# -----------------------------

def extract_first_json_object(text: str) -> dict:
    """Extract the first complete {...} JSON object from a text blob."""
    text = (text or "").strip()

    # remove ```json fences (if the model used them)
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
                return json.loads(text[start:i + 1])

    raise ValueError("no complete JSON object found")


def parse_json_object(text: str) -> Dict[str, Any]:
    obj = extract_first_json_object(text)
    if not isinstance(obj, dict):
        raise ValueError("JSON root must be an object")
    return obj


def normalize_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# -----------------------------
# Codex app-server: safe event parsing
# -----------------------------

def find_codex() -> Optional[str]:
    return shutil.which("codex") or shutil.which("codex.cmd")


def _safe_event_json(line: str) -> Optional[Dict[str, Any]]:
    """Event stream lines from codex app-server should be pure JSON per line."""
    line = (line or "").strip()
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
    full_text: str          # everything text-like we collected during this turn
    assistant_text: str     # best guess "final assistant message" (agentMessage/assistantMessage)
    items: List[Dict[str, Any]]
    events_count: int
    last_event: Dict[str, Any]


@dataclass
class CodexRoleClient:
    """
    Persistent codex app-server per role.

    OPTION-2 logging:
      - buffer all text-like outputs in the turn
      - capture best "assistant message"
      - return TurnResult (full_text + assistant_text)
    """
    role_name: str
    model: str = "gpt-5.2-codex"

    proc: Optional[subprocess.Popen] = None
    inbox: "queue.Queue[Dict[str, Any]]" = field(default_factory=queue.Queue)
    thread_id: Optional[str] = None
    _req_id: int = 100

    # per-turn buffers
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
                msg = _safe_event_json(raw.decode("utf-8", errors="replace"))
                if msg is not None:
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

        trimmed = dict(item) if isinstance(item, dict) else {"type": itype_raw}
        if "aggregatedOutput" in trimmed and isinstance(trimmed["aggregatedOutput"], str):
            trimmed["aggregatedOutput"] = trimmed["aggregatedOutput"][:8000] + "…"
        self._items.append(trimmed)

        if txt:
            self._turn_text_parts.append(f"[{itype_raw}] {txt}")

        # capture likely "final assistant response"
        if itype in ("agentmessage", "assistantmessage"):
            if txt:
                self._assistant_text = txt

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

            if method == "item/completed":
                self._collect_item_completed(msg)

            if method == "turn/completed":
                full_text = "\n\n".join(self._turn_text_parts).strip()
                assistant_text = (self._assistant_text or "").strip()
                if not assistant_text and not full_text:
                    log(f"[{self.role_name}] WARNING: turn completed but no text captured.")
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
# Roles
# -----------------------------

@dataclass
class RoleSpec:
    name: str
    model: str
    system_instructions: str


ROLE_SPECS: List[RoleSpec] = [
    RoleSpec(
        name="planner",
        model=os.environ.get("PLANNER_MODEL", "gpt-5.2-codex"),
        system_instructions=(
            "Du bist PLANNER. Du koordinierst fachlich: planst, priorisierst, delegierst. "
            "Du entscheidest next_owner. Keine Tools/Commands. Kein Repo-Scan."
        ),
    ),
    RoleSpec(
        name="architect",
        model=os.environ.get("ARCHITECT_MODEL", "gpt-5.2-codex"),
        system_instructions=(
            "Du bist ARCHITECT. Du analysierst Integrationspunkte und entwirfst Architektur/Interfaces. "
            "Tiefe Analyse gehört in analysis_md (Markdown). Übergabe an Planner als kleines JSON."
        ),
    ),
    RoleSpec(
        name="implementer",
        model=os.environ.get("IMPLEMENTER_MODEL", "gpt-5.2-codex"),
        system_instructions=(
            "Du bist IMPLEMENTER. Du entwirfst konkrete Änderungen (Dateien/Code/Tests) als Plan. "
            "Tiefe Analyse/Erklärung in analysis_md (Markdown). Übergabe klein halten."
        ),
    ),
    RoleSpec(
        name="integrator",
        model=os.environ.get("INTEGRATOR_MODEL", "gpt-5.2-codex"),
        system_instructions=(
            "Du bist INTEGRATOR/VERIFIER. Du prüfst Risiken, Tests, Integrationsplan und gibst DONE/CONTINUE. "
            "Tiefe Analyse in analysis_md (Markdown). Übergabe an Planner klein halten."
        ),
    ),
]


# -----------------------------
# Controller / Orchestrator
# -----------------------------

@dataclass
class OrchestratorConfig:
    goal: str
    cycles: int = 2
    repair_attempts: int = 1


def json_contract_instruction() -> str:
    return (
        "\n\nFORMAT-VERTRAG (streng):\n"
        "- Antworte mit GENAU EINEM gültigen JSON-Objekt.\n"
        "- KEIN Text außerhalb des JSON. KEIN Markdown-Codefence.\n"
        "- Wenn etwas unklar ist: gib JSON mit Feld \"error\" zurück.\n"
    )


def role_output_schema_hint(role: str) -> str:
    # Minimal + includes path placeholder (controller will fill the real path)
    if role == "planner":
        return (
            "\nJSON-SCHEMA-HINWEIS (planner, klein):\n"
            "{\n"
            "  \"plan\": {\"steps\": [...]},\n"
            "  \"next_owner\": \"architect|implementer|integrator\",\n"
            "  \"notes\": \"kurz\"\n"
            "}\n"
        )
    return (
        f"\nJSON-SCHEMA-HINWEIS ({role}, klein + markdown):\n"
        "{\n"
        "  \"summary\": \"max 5 Zeilen\",\n"
        "  \"key_points\": [\"...\"],\n"
        "  \"requests\": {\"need_more_context\": false, \"files\": [], \"why\": \"\"},\n"
        "  \"analysis_md\": \"(Markdown, beliebig lang)\",\n"
        "  \"analysis_md_path\": \"(wird vom Controller gesetzt)\",\n"
        "  \"next_owner_suggestion\": \"planner\"\n"
        "}\n"
    )


def ensure_single_json_object(
    client: CodexRoleClient,
    prompt: str,
    attempts: int,
    timeout_s: float,
) -> Dict[str, Any]:
    last_text = ""
    for n in range(attempts + 1):
        tr = client.run_turn(prompt, timeout_s=timeout_s)
        last_text = tr.assistant_text or tr.full_text
        try:
            return parse_json_object(last_text)
        except Exception:
            pass

        if n < attempts:
            prompt = (
                "Deine letzte Antwort war KEIN gültiges JSON-Objekt.\n"
                "Bitte liefere GENAU EIN JSON-Objekt und sonst nichts.\n"
                + json_contract_instruction()
            )

    raise RuntimeError(f"{client.role_name}: no valid JSON object produced. Last text:\n{last_text[:4000]}")


class CodexRunsOrchestrator:
    """
    Controller:
    - runs planner -> architect -> implementer -> integrator -> planner loop
    - writes deep analysis markdown to .runs
    - forwards only reduced JSON to keep context small
    """

    def __init__(self, role_specs: List[RoleSpec], cfg: OrchestratorConfig):
        self.cfg = cfg
        self.run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self.runs_dir = Path(".runs") / self.run_id
        self.runs_dir.mkdir(parents=True, exist_ok=True)

        self.clients: Dict[str, CodexRoleClient] = {
            rs.name: CodexRoleClient(role_name=rs.name, model=rs.model) for rs in role_specs
        }
        self.specs: Dict[str, RoleSpec] = {rs.name: rs for rs in role_specs}
        self.pipeline: List[str] = [rs.name for rs in role_specs]  # planner first by design

        self.state: Dict[str, Any] = {
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

    def _write_run_file(self, rel_path: str, content: str) -> str:
        p = self.runs_dir / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content or "", encoding="utf-8")
        return str(p)

    def _store_turn_artifacts(self, role: str, turn: TurnResult, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Store:
          - prompt/response artifacts (optional: we store full_text)
          - analysis markdown from payload["analysis_md"] if present
        Return reduced payload with analysis_md_path (and analysis_md removed).
        """
        role_dir = f"{role}/turn_{turn.request_id}"
        # always store full output for debugging/audit
        self._write_run_file(f"{role_dir}/full_output.md", turn.full_text or turn.assistant_text or "")

        reduced = dict(payload)

        analysis_md = reduced.pop("analysis_md", None)
        if isinstance(analysis_md, str) and analysis_md.strip():
            md_path = self._write_run_file(f"{role_dir}/analysis.md", analysis_md.strip() + "\n")
            reduced["analysis_md_path"] = md_path
        else:
            # still provide a path to the full_output for traceability
            reduced.setdefault("analysis_md_path", str(self.runs_dir / f"{role_dir}/full_output.md"))

        # store the reduced JSON itself
        self._write_run_file(f"{role_dir}/handoff.json", normalize_json(reduced))

        return reduced

    def _build_prompt(self, role: str, incoming_json: Optional[Dict[str, Any]]) -> str:
        spec = self.specs[role]
        base = (
            f"Rolle: {role}\n"
            f"{spec.system_instructions}\n\n"
            f"Ziel:\n{self.cfg.goal}\n"
        )

        if incoming_json:
            base += "\nInput (reduziertes JSON, klein halten):\n" + normalize_json(incoming_json) + "\n"

        base += json_contract_instruction()
        base += role_output_schema_hint(role)

        # extra safety to prevent repo/tool chatter
        base += (
            "\nREGELN:\n"
            "- KEIN Repo-Scan, keine 'AGENTS.md' Suche.\n"
            "- KEINE Tools/Commands ausführen.\n"
            "- Tiefe Analyse NUR in analysis_md (Markdown-String im JSON).\n"
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
                    client = self.clients[role]
                    prompt = self._build_prompt(role, incoming)

                    # run + parse JSON
                    turn = client.run_turn(prompt, timeout_s=240.0 if role != "planner" else 180.0)
                    payload = ensure_single_json_object(client, prompt, attempts=self.cfg.repair_attempts,
                                                        timeout_s=240.0 if role != "planner" else 180.0)

                    # store markdown + reduce payload (for small context)
                    reduced = self._store_turn_artifacts(role, turn, payload)

                    self.state["latest_json_by_role"][role] = reduced
                    self.state["history"].append({"role": role, "turn": turn.request_id, "handoff": reduced})

                    # handoff goes forward (small JSON only)
                    incoming = reduced

                    # stop condition
                    if role == "integrator" and self._is_done(reduced):
                        log("Integrator indicates DONE. Stopping.")
                        return

                # after integrator, loop continues back to planner with last reduced JSON

        finally:
            self.stop_all()
            # store controller state summary
            (self.runs_dir / "controller_state.json").write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


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

    orch = CodexRunsOrchestrator(ROLE_SPECS, cfg)
    log("Starting Codex runs orchestrator (.runs storage + reduced JSON handoffs)...")
    log(f"Goal: {goal}")
    log(f"Roles: {', '.join(orch.pipeline)}")
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
