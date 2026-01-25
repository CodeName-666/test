"""Utilities that interact with the underlying operating system."""
from __future__ import annotations

import shutil
from typing import Optional


class SystemLocator:
    """Locate system-level executables needed by the orchestrator."""

    def find_codex(self) -> Optional[str]:
        """Locate the codex CLI binary available in PATH."""
        codex_binary = shutil.which("codex") or shutil.which("codex.cmd")
        return codex_binary


DEFAULT_SYSTEM_LOCATOR = SystemLocator()
