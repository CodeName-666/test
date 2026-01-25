"""Lightweight logging helpers for the orchestrator."""
import time


def log(msg: str) -> None:
    """Print a timestamped message synchronously."""
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
