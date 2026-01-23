#!/usr/bin/env python3
"""
codex_multi_role_orchestrator.py

A clean, extensible baseline orchestrator that uses ONLY OpenAI Codex CLI for ALL roles.
It spins up one persistent `codex app-server` process per role and routes messages through
a configurable role pipeline.

Key design goals:
- Only Codex CLI (no Claude/Gemini).
- Easy to extend: add a role by adding an entry to ROLE_SPECS.
- Data-plane uses TOON blocks inside the messages (optional but recommended).
- Control-plane uses codex app-server JSONL events.

Requirements:
- Python 3.10+
- `codex` in PATH
- OPENAI_API_KEY set in env (typical)
"""

from __future__ import annotations

import json
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
# Logging
# -----------------------------

def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# -----------------------------
# Codex path helper
# -----------------------------

def find_codex() -> Optional[str]:
    """
    Find the Codex CLI executable, Windows-friendly (codex or codex.cmd).
    """
    return shutil.which("codex") or shutil.which("codex.cmd")

# -----------------------------
# TOON helpers (data-plane)
# -----------------------------

TOON_BEGIN = "BEGIN_TOON"
TOON_END = "END_TOON"

def wrap_toon(payload: str) -> str:
    payload = (payload or "").strip()
    return f"{TOON_BEGIN}\n{payload}\n{TOON_END}"

def extract_toon_blocks(text: str) -> List[str]:
    if not text:
        return []
    blocks: List[str] = []
    start = 0
    while True:
        i = text.find(TOON_BEGIN, start)
        if i == -1:
            break
        j = text.find(TOON_END, i + len(TOON_BEGIN))
        if j == -1:
            break
        block = text[i + len(TOON_BEGIN): j].strip()
        if block:
            blocks.append(block)
        start = j + len(TOON_END)
    return blocks

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
class CodexRoleClient:
    """
    One persistent Codex app-server per role.
    """
    role_name: str
    model: str = "gpt-5.2-codex"

    proc: Optional[subprocess.Popen] = None
    inbox: "queue.Queue[Dict[str, Any]]" = field(default_factory=queue.Queue)
    thread_id: Optional[str] = None

    _req_id: int = 100
    _last_agent_message: Optional[str] = None

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

    def run_turn(self, prompt: str, timeout_s: float = 180.0) -> Tuple[str, Dict[str, Any]]:
        """
        Synchronously run one turn, return (final_text, last_event).
        """
        self.start()
        assert self.thread_id

        self._req_id += 1
        rid = self._req_id
        self._last_agent_message = None

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

            last_event = msg
            m = msg.get("method")

            if m == "item/completed":
                item = (msg.get("params") or {}).get("item") or {}
                if item.get("type") == "agent_message":
                    self._last_agent_message = item.get("text")

            if m == "turn/completed":
                return (self._last_agent_message or "", last_event)

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
    enforce_toon: bool = True
    repair_attempts: int = 1

def toon_instruction() -> str:
    return (
        "\n\nWICHTIG: Antworte mit genau einem TOON-Block:\n"
        f"{TOON_BEGIN}\n"
        "...TOON payload...\n"
        f"{TOON_END}\n"
        "Außerhalb des TOON-Blocks: maximal 1-2 kurze Zeilen oder nichts.\n"
    )

def ensure_single_toon_block(
    client: CodexRoleClient,
    prompt: str,
    attempts: int,
    timeout_s: float,
) -> str:
    """
    Runs a turn, ensures at least one TOON block exists; if missing, asks to repair.
    Returns the TOON payload (block content).
    """
    last_text = ""
    for n in range(attempts + 1):
        text, _ = client.run_turn(prompt, timeout_s=timeout_s)
        last_text = text
        blocks = extract_toon_blocks(text)
        if len(blocks) >= 1:
            # take first block; keep it simple
            return blocks[0]

        if n < attempts:
            prompt = (
                "Deine letzte Antwort war NICHT im erwarteten TOON-Format.\n"
                "Bitte liefere GENAU EINEN gültigen TOON-Block und sonst nichts.\n"
                + toon_instruction()
            )

    raise RuntimeError(f"{client.role_name}: no TOON block produced. Last text:\n{last_text[:4000]}")

class CodexPipelineOrchestrator:
    def __init__(self, role_specs: List[RoleSpec], cfg: OrchestratorConfig):
        self.cfg = cfg

        self.clients: Dict[str, CodexRoleClient] = {
            rs.name: CodexRoleClient(role_name=rs.name, model=rs.model)
            for rs in role_specs
        }
        self.specs: Dict[str, RoleSpec] = {rs.name: rs for rs in role_specs}
        self.pipeline: List[str] = [rs.name for rs in role_specs]

        # shared memory (TOON objects as strings)
        self.memory: Dict[str, Any] = {
            "goal": cfg.goal,
            "latest_toon_by_role": {},  # role -> toon payload string
            "history": [],              # list of {role, toon_preview}
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

    def _build_prompt(self, role: str, incoming_toon: Optional[str]) -> str:
        spec = self.specs[role]
        base = (
            f"Rolle: {role}\n"
            f"{spec.system_instructions}\n\n"
            f"Ziel:\n{self.cfg.goal}\n"
        )

        if incoming_toon:
            base += "\nInput von vorheriger Rolle (TOON):\n" + wrap_toon(incoming_toon) + "\n"

        if self.cfg.enforce_toon:
            base += toon_instruction()

        # A tiny contract suggestion for each role’s TOON:
        base += (
            "\nHinweis für TOON-Struktur (Empfehlung):\n"
            "- planner: steps/tasks/next_owner\n"
            "- architect: modules/apis/files/tasks\n"
            "- implementer: changes/files/tests/commands\n"
            "- integrator: checklist/issues/status(DONE|CONTINUE)/next_owner\n"
        )
        return base

    def run(self) -> None:
        self.start_all()
        log("All roles started.")

        # Kickoff: planner has no incoming TOON
        incoming: Optional[str] = None

        try:
            for cycle in range(1, self.cfg.cycles + 1):
                log(f"=== Cycle {cycle}/{self.cfg.cycles} ===")

                for idx, role in enumerate(self.pipeline):
                    client = self.clients[role]
                    prompt = self._build_prompt(role, incoming)

                    if self.cfg.enforce_toon:
                        toon_payload = ensure_single_toon_block(
                            client=client,
                            prompt=prompt,
                            attempts=self.cfg.repair_attempts,
                            timeout_s=240.0 if role in ("implementer", "integrator") else 180.0,
                        )
                        self.memory["latest_toon_by_role"][role] = toon_payload
                        self._remember(role, toon_payload)
                        incoming = toon_payload
                    else:
                        text, _ = client.run_turn(prompt)
                        incoming = text  # not recommended

                    # Early stop if integrator says DONE (simple heuristic)
                    if role == "integrator":
                        if "DONE" in (self.memory["latest_toon_by_role"].get("integrator") or ""):
                            log("Integrator indicates DONE. Stopping.")
                            return

                # After finishing pipeline, feed last output back into planner next cycle
                # (incoming already set to last role’s TOON)

        finally:
            self.stop_all()

    def _remember(self, role: str, toon_payload: str) -> None:
        preview = toon_payload.strip().replace("\n", " ")
        preview = preview[:220] + ("..." if len(preview) > 220 else "")
        self.memory["history"].append({"role": role, "preview": preview})
        log(f"{role}: TOON {preview}")


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
        enforce_toon=os.environ.get("ENFORCE_TOON", "1") == "1",
        repair_attempts=int(os.environ.get("REPAIR_ATTEMPTS", "1")),
    )

    orch = CodexPipelineOrchestrator(ROLE_SPECS, cfg)

    log("Starting Codex-only multi-role orchestrator...")
    log(f"Goal: {goal}")
    log(f"Roles: {', '.join(orch.pipeline)}")
    log("Customize via env: GOAL, CYCLES, ENFORCE_TOON, REPAIR_ATTEMPTS, *_MODEL")
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
