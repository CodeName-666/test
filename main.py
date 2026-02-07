#!/usr/bin/env python3
"""Entrypoint for the Codex multi-role orchestrator."""
from __future__ import annotations

import sys
from typing import List

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

from defaults import (
    DEFAULT_CYCLES,
    DEFAULT_GOAL,
    DEFAULT_OPENAI_API_KEY,
    DEFAULT_PYTEST_CMD,
    DEFAULT_REPAIR_ATTEMPTS,
    DEFAULT_RUN_TESTS,
)
from codex_multi_role.utils.env_utils import env_flag, env_int, env_str
from codex_multi_role.runtime.orchestrator_config import OrchestratorConfig
from codex_multi_role.communication import ConsoleUserInteraction
from codex_multi_role.dynamic import DynamicOrchestrator
from codex_multi_role.roles.role_spec import RoleSpecCatalog
from codex_multi_role.roles.role_spec_models import RoleSpec
from codex_multi_role.utils.env_utils import EnvironmentReader
from codex_multi_role.logging import TimestampLogger

from codex_multi_role.utils.system_utils import find_codex


def create_orchestrator(
    role_specs: List[RoleSpec],
    cfg: OrchestratorConfig,
    role_spec_catalog: RoleSpecCatalog,
    logger: TimestampLogger,
) -> DynamicOrchestrator:
    """Factory function to create the appropriate orchestrator.

    Args:
        role_specs: List of role specifications.
        cfg: Orchestrator configuration.
        role_spec_catalog: Role specification catalog.
        logger: Logger instance.

    Returns:
        DynamicOrchestrator instance.
    """
    logger.log("Using dynamic orchestrator mode")
    user_interaction = ConsoleUserInteraction(
        auto_use_defaults=env_flag("AUTO_USE_DEFAULTS", "0"),
    )
    return DynamicOrchestrator(
        role_specs,
        cfg,
        user_interaction=user_interaction,
        role_spec_catalog=role_spec_catalog,
    )


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    logger = TimestampLogger()

    if load_dotenv:
        load_dotenv()
    else:
        logger.log(
            "WARN: python-dotenv not installed; .env will not be loaded automatically."
        )

    environment_reader = EnvironmentReader()
    environment_reader.apply_defaults_to_environment()

    if not find_codex():
        raise SystemExit("codex CLI not found in PATH")

    api_key = env_str("OPENAI_API_KEY", DEFAULT_OPENAI_API_KEY)
    if not api_key:
        logger.log("WARN: OPENAI_API_KEY is not set. Codex CLI typically needs it.")

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

    role_spec_catalog = RoleSpecCatalog(environment_reader=environment_reader)
    role_specs = role_spec_catalog.build_role_specs()

    orchestrator = create_orchestrator(
        role_specs,
        cfg,
        role_spec_catalog,
        logger,
    )

    logger.log("Starting Codex orchestrator...")
    logger.log("Mode: dynamic")
    logger.log(f"Goal: {goal}")
    logger.log(f"Artifacts: .runs/{orchestrator.run_id}/...")
    logger.log("Stop with Ctrl+C.\n")

    try:
        orchestrator.run()
    except KeyboardInterrupt:
        logger.log("Interrupted.")
    finally:
        orchestrator.stop_all()
        logger.log("Done.")


if __name__ == "__main__":
    main()
