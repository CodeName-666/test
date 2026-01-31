"""Utilities that interact with the underlying operating system."""
from __future__ import annotations

import shutil
from typing import Optional


class SystemLocator:
    """Locate system-level executables needed by the orchestrator."""

    def find_codex(self) -> Optional[str]:
        """Locate the codex CLI binary available in PATH.

        Returns:
            Absolute path to the first matching codex binary, or None if not found.
        """
        import defaults

        codex_binary = None
        for binary_name in defaults.CODEX_BINARY_NAMES:
            candidate = shutil.which(binary_name)
            if candidate:
                codex_binary = candidate
                break
        return codex_binary


def find_codex() -> Optional[str]:
    """Compatibility helper to locate the codex CLI binary."""
    return SystemLocator().find_codex()
