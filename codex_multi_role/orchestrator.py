"""Core orchestrator that runs multiple Codex roles in cycles."""
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .codex_role_client import CodexRoleClient
from .env_utils import env_float
from .json_utils import normalize_json, parse_json_object_from_assistant_text
from .logging import log
from .role_spec import RoleSpec, json_contract_instruction, schema_hint_non_json
from .orchestrator_config import OrchestratorConfig
from .turn_result import TurnResult


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
            client = CodexRoleClient(role_name=rs.name, model=rs.model, reasoning_effort=rs.reasoning_effort)
            role_events = self.runs_dir / rs.name / "events.jsonl"
            role_events.parent.mkdir(parents=True, exist_ok=True)
            client.events_file = role_events
            self.clients[rs.name] = client

        self.state: Dict[str, Any] = {
            "goal": cfg.goal,
            "latest_json_by_role": {},
            "history": [],
        }

    def start_all(self) -> None:
        for role in self.pipeline:
            self.clients[role].start()

    def stop_all(self) -> None:
        for client in self.clients.values():
            try:
                client.stop()
            except Exception:
                pass

    def _write_text(self, rel_path: str, content: str) -> str:
        target = self.runs_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content or "", encoding="utf-8")
        return str(target)

    def _build_prompt(self, role: str, incoming: Optional[Dict[str, Any]]) -> str:
        spec = self.specs[role]
        base = (
            f"Rolle: {role}\n"
            f"{spec.system_instructions}\n\n"
            f"Ziel:\n{self.cfg.goal}\n"
        )
        if incoming:
            base += "\nInput (reduziertes JSON, klein halten):\n" + normalize_json(incoming) + "\n"
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

    def _run_and_parse_json_strict(
        self, role: str, prompt: str, timeout_s: float
    ) -> Tuple[TurnResult, Dict[str, Any]]:
        last_assistant_text = ""
        last_turn: Optional[TurnResult] = None

        for attempt in range(self.cfg.repair_attempts + 1):
            turn = self.clients[role].run_turn(prompt, timeout_s=timeout_s)
            last_turn = turn
            last_assistant_text = (turn.assistant_text or "").strip()

            turn_dir = f"{role}/turn_{turn.request_id}"
            self._write_text(f"{turn_dir}/assistant_text.txt", turn.assistant_text or "")
            self._write_text(f"{turn_dir}/delta_text.txt", turn.delta_text or "")
            self._write_text(f"{turn_dir}/items_text.md", turn.full_items_text or "")
            self._write_text(f"{turn_dir}/prompt.txt", prompt)

            if not last_assistant_text:
                if attempt < self.cfg.repair_attempts:
                    prompt = (
                        "Deine letzte Antwort konnte nicht als Assistant-Text erfasst werden.\n"
                        "Bitte liefere GENAU EIN JSON-Objekt und sonst nichts.\n"
                        + json_contract_instruction()
                    )
                    continue
                raise RuntimeError(
                    f"{role}: assistant_text missing after turn completion; refusing to parse prompt/full_text."
                )

            try:
                payload = parse_json_object_from_assistant_text(last_assistant_text)
                return turn, payload
            except Exception:
                if attempt < self.cfg.repair_attempts:
                    prompt = (
                        "Deine letzte Antwort war KEIN gültiges JSON-Objekt.\n"
                        "Bitte liefere GENAU EIN JSON-Objekt und sonst nichts.\n"
                        + json_contract_instruction()
                    )
                    continue
                raise RuntimeError(
                    f"{role}: invalid JSON in assistant_text. First 2000 chars:\n{last_assistant_text[:2000]}"
                )

        raise RuntimeError(f"{role}: failed to get valid JSON")

    def _reduce_and_store_payload(self, role: str, turn: TurnResult, payload: Dict[str, Any]) -> Dict[str, Any]:
        turn_dir = f"{role}/turn_{turn.request_id}"
        reduced = dict(payload)

        analysis_md = reduced.pop("analysis_md", None)
        if isinstance(analysis_md, str) and analysis_md.strip():
            md_path = self._write_text(f"{turn_dir}/analysis.md", analysis_md.strip() + "\n")
            reduced["analysis_md_path"] = md_path
        else:
            reduced["analysis_md_path"] = str(self.runs_dir / f"{turn_dir}/items_text.md")

        self._write_text(f"{turn_dir}/handoff.json", normalize_json(reduced))
        return reduced

    def _apply_implementer_files(self, reduced_payload: Dict[str, Any], turn_dir: str) -> None:
        files = reduced_payload.get("files")
        if not isinstance(files, list):
            return

        applied: List[Dict[str, Any]] = []
        for entry in files:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            content = entry.get("content")
            if not isinstance(path, str) or not path.strip():
                continue
            if not isinstance(content, str):
                continue

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
            process = subprocess.run(cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
            output = process.stdout.decode("utf-8", errors="replace")
            self._write_text("tests/pytest_output.txt", output)
            self._write_text("tests/pytest_rc.txt", str(process.returncode))
            log(f"[tests] pytest rc={process.returncode}")
        except Exception as exc:
            self._write_text("tests/pytest_error.txt", str(exc))
            log(f"[tests] pytest failed to run: {exc}")

    def run(self) -> None:
        self.start_all()
        log(f"Run folder: {self.runs_dir}")

        incoming: Optional[Dict[str, Any]] = None
        planner_timeout = env_float("PLANNER_TIMEOUT_S", "240")
        role_timeout = env_float("ROLE_TIMEOUT_S", "600")

        try:
            for cycle in range(1, self.cfg.cycles + 1):
                log(f"=== Cycle {cycle}/{self.cfg.cycles} ===")

                for role in self.pipeline:
                    prompt = self._build_prompt(role, incoming)
                    timeout = planner_timeout if role == "planner" else role_timeout

                    turn, payload = self._run_and_parse_json_strict(role, prompt, timeout_s=timeout)
                    reduced = self._reduce_and_store_payload(role, turn, payload)

                    if role == "implementer":
                        turn_dir = f"{role}/turn_{turn.request_id}"
                        self._apply_implementer_files(reduced, turn_dir)
                        self._run_tests_if_enabled()

                    self.state["latest_json_by_role"][role] = reduced
                    self.state["history"].append({"role": role, "turn": turn.request_id, "handoff": reduced})
                    incoming = reduced

                    if role == "integrator":
                        status_value = reduced.get("status")
                        if isinstance(status_value, str) and status_value.strip().upper() == "DONE":
                            log("Integrator indicates DONE. Stopping.")
                            return

        finally:
            (self.runs_dir / "controller_state.json").write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.stop_all()
