"""Data structure that holds the result of a Codex turn."""
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class TurnResult:
    role: str
    request_id: int
    assistant_text: str
    delta_text: str
    full_items_text: str
    events_count: int
    last_event: Dict[str, Any]
