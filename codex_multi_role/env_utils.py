"""Environment helpers that parse typed CLI/config values."""
import os


def env_int(name: str, default: str) -> int:
    try:
        return int(os.environ.get(name, default).strip())
    except Exception:
        return int(default)


def env_float(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default).strip())
    except Exception:
        return float(default)


def env_flag(name: str, default: str = "0") -> bool:
    val = os.environ.get(name, default).strip().lower()
    return val in ("1", "true", "yes", "on")
