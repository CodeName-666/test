"""Data structure that holds the result of a Codex turn."""
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class TurnResult:
    """Snapshot of a completed Codex turn."""

    # Role that produced the result.
    role: str
    # Request identifier used for the turn.
    request_id: int
    # Final assistant text extracted from the stream.
    assistant_text: str
    # Aggregated delta text as it streamed in.
    delta_text: str
    # Concatenated full item text across completion events.
    full_items_text: str
    # Total number of events observed during the turn.
    events_count: int
    # The last raw event received before completion.
    last_event: Dict[str, Any]
