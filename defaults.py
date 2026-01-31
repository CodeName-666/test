"""Central defaults for the Codex multi-role orchestrator."""
from __future__ import annotations

# Env/config defaults
DEFAULT_CONFIG_DIRECTORY = "config"
DEFAULT_CONFIG_FILENAME = "main.yaml"
TRUTHY_FLAG_VALUES = ("1", "true", "yes", "on")

DEFAULT_OPENAI_API_KEY = ""
DEFAULT_GOAL = (
    "Implementiere diese codex_multi_role_3_gen.py Datei komplett neu. "
    "Teile dabei das Skript in separate Dateien auf. Jede Klasse soll eine eigene "
    "Datei bekommen. Funktionen sollen strukturiert und Uebersichtlich aufgebaut sein."
)

# Orchestrator config defaults
DEFAULT_CYCLES = 2
DEFAULT_REPAIR_ATTEMPTS = 1
DEFAULT_RUN_TESTS = False
DEFAULT_PYTEST_CMD = "python -m pytest"

# Orchestrator runtime defaults
PYTEST_CMD_ENV = "PYTEST_CMD"
PLANNER_TIMEOUT_ENV = "PLANNER_TIMEOUT_S"
ROLE_TIMEOUT_ENV = "ROLE_TIMEOUT_S"

DEFAULT_PLANNER_TIMEOUT_S = "240"
DEFAULT_ROLE_TIMEOUT_S = "600"

# Codex role client defaults
FULL_ACCESS = False

ENV_AUTO_APPROVE_FILE_CHANGES = "CODEX_AUTO_APPROVE_FILE_CHANGES"
ENV_ALLOW_COMMANDS = "CODEX_ALLOW_COMMANDS"
ENV_AUTO_APPROVE_COMMANDS = "CODEX_AUTO_APPROVE_COMMANDS"
ENV_HARD_TIMEOUT_S = "HARD_TIMEOUT_S"

DEFAULT_AUTO_APPROVE_FILE_CHANGES = "1"
DEFAULT_ALLOW_COMMANDS = "1"
DEFAULT_AUTO_APPROVE_COMMANDS = "0"
DEFAULT_HARD_TIMEOUT_S = "0"

# System defaults
CODEX_BINARY_NAMES = ("codex", "codex.cmd")

# Logging defaults
DEFAULT_TIMESTAMP_FORMAT = "%H:%M:%S"

# JSON defaults
DEFAULT_CODE_FENCE_PATTERN = r"`(?:json)?\s*"

# Role spec defaults
DEFAULT_MODEL_NAME = "gpt-5.1-codex-mini"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_MODEL_ENV = "DEFAULT_MODEL"
ROLE_CONFIG_ENV = "ROLE_CONFIG_PATH"
ROLE_CONFIG_FILENAME = "developer_config.yaml"
ROLE_CONFIG_DIRECTORY = "config"

CONFIG_KEY_DEFAULTS = "defaults"
CONFIG_KEY_GENERAL_PROMPTS = "general_prompts"
CONFIG_KEY_SCHEMA_HINTS = "schema_hints"
CONFIG_KEY_ROLES = "roles"
CONFIG_KEY_PROMPT_FLAGS = "prompt_flags"
CONFIG_KEY_BEHAVIORS = "behaviors"
CONFIG_KEY_REASONING_EFFORT = "reasoning_effort"
CONFIG_KEY_PROMPT_TEXT = "prompt_text"
CONFIG_KEY_PROMPT_FILE = "prompt_file"
CONFIG_KEY_SKILLS = "skills"
CONFIG_KEY_ROLE_FILE = "role_file"
CONFIG_KEY_MODEL = "model"
CONFIG_KEY_MODEL_ENV = "model_env"
CONFIG_KEY_NAME = "name"
GENERAL_PROMPT_JSON_CONTRACT = "json_contract"
SCHEMA_HINT_DEFAULT_KEY = "default"

# Default instances
from codex_multi_role.utils.env_utils import EnvironmentReader

DEFAULT_ENVIRONMENT = EnvironmentReader()

from codex_multi_role.utils.event_utils import EventParser

DEFAULT_EVENT_PARSER = EventParser()

from codex_multi_role.utils.json_utils import JsonPayloadFormatter

DEFAULT_JSON_FORMATTER = JsonPayloadFormatter()

from codex_multi_role.logging import TimestampLogger

DEFAULT_LOGGER = TimestampLogger(DEFAULT_TIMESTAMP_FORMAT)

from codex_multi_role.utils.system_utils import SystemLocator

DEFAULT_SYSTEM_LOCATOR = SystemLocator()

from codex_multi_role.role_spec import RoleSpecCatalog

DEFAULT_ROLE_SPEC_CATALOG = RoleSpecCatalog()
ROLE_SPECS = DEFAULT_ROLE_SPEC_CATALOG.build_role_specs()
