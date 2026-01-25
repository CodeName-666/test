"""Utilities that interact with the underlying operating system."""
import shutil
from typing import Optional


def find_codex() -> Optional[str]:
    """Locate the codex CLI binary available in PATH."""
    bin_name = shutil.which("codex") or shutil.which("codex.cmd")
    return bin_name
