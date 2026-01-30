#!/usr/bin/env python3
"""Entrypoint for the Codex multi-role orchestrator."""
from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

from defaults import (
    DEFAULT_ENVIRONMENT,
    DEFAULT_GOAL,
    DEFAULT_OPENAI_API_KEY,
)
from codex_multi_role.env_utils import env_flag, env_int, env_str
from codex_multi_role.logging import log
from codex_multi_role.orchestrator import CodexRunsOrchestratorV2
from codex_multi_role.data.orchestrator_config import OrchestratorConfig
from defaults import (
    DEFAULT_CYCLES,
    DEFAULT_PYTEST_CMD,
    DEFAULT_REPAIR_ATTEMPTS,
    DEFAULT_RUN_TESTS,
    ROLE_SPECS,
)

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

    DEFAULT_ENVIRONMENT.apply_defaults_to_environment()

    if not find_codex():
        raise SystemExit("codex CLI not found in PATH")

    api_key = env_str("OPENAI_API_KEY", DEFAULT_OPENAI_API_KEY)
    if not api_key:
        log("WARN: OPENAI_API_KEY is not set. Codex CLI typically needs it.")

    goal = env_str(
        "GOAL",
        DEFAULT_GOAL,
    )

    run_tests_default = "1" if DEFAULT_RUN_TESTS else "0"
    cfg = OrchestratorConfig(
        goal=goal,
        cycles=env_int("CYCLES", str(DEFAULT_CYCLES)),
        repair_attempts=env_int("REPAIR_ATTEMPTS", str(DEFAULT_REPAIR_ATTEMPTS)),
        run_tests=env_flag("RUN_TESTS", run_tests_default),
        pytest_cmd=env_str("PYTEST_CMD", DEFAULT_PYTEST_CMD),
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
