#!/usr/bin/env python3
"""Entrypoint for the Codex multi-role orchestrator."""
from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

from codex_multi_role.env_utils import env_flag, env_int
from codex_multi_role.logging import log
from codex_multi_role.orchestrator import CodexRunsOrchestratorV2
from codex_multi_role.orchestrator_config import OrchestratorConfig
from codex_multi_role.role_spec import ROLE_SPECS
from codex_multi_role.system_utils import find_codex


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

    goal = os.environ.get(
        "GOAL",
        "Implementiere diese codex_multi_role_3_gen.py Datei komplett neu. Teile dabei das Skript in separate Dateien auf. Jede Klasse soll eine eigene Datei bekommen. Funktionen sollen strukturiert und Uebersichtlich aufgebaut sein.",
    )

    cfg = OrchestratorConfig(
        goal=goal,
        cycles=env_int("CYCLES", "2"),
        repair_attempts=env_int("REPAIR_ATTEMPTS", "1"),
        run_tests=env_flag("RUN_TESTS", "0"),
        pytest_cmd=os.environ.get("PYTEST_CMD", "python -m pytest"),
    )

    orchestrator = CodexRunsOrchestratorV2(ROLE_SPECS, cfg)

    log("Starting Codex orchestrator (modularized version)...")
    log(f"Goal: {goal}")
    log("Roles: %s" % ", ".join(orchestrator.pipeline))
    log("Reasoning effort: from ROLE_SPECS")
    log(f"Artifacts: .runs/{orchestrator.run_id}/...")
    log("Stop with Ctrl+C.\n")

    try:
        orchestrator.run()
    except KeyboardInterrupt:
        log("Interrupted.")
    finally:
        orchestrator.stop_all()
        log("Done.")


if __name__ == "__main__":
    main()
