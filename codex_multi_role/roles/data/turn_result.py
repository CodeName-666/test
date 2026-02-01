"""Data structure that holds the result of a Codex turn."""
from dataclasses import dataclass
from typing import Any, Dict

from ...utils.validation_utils import ValidationMixin


@dataclass
class TurnResult(ValidationMixin):
    """Snapshot of a completed Codex turn.

    Attributes:
        role: Role name that produced the result.
        request_id: Request identifier used for the turn.
        assistant_text: Final assistant text extracted from the stream.
        delta_text: Aggregated streaming delta text.
        full_items_text: Concatenated item text across completion events.
        events_count: Total number of events observed during the turn.
        last_event: The last raw event received before completion.

    Raises:
        TypeError: If any field has an invalid type.
        ValueError: If role is empty or request_id is negative.
    """

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

    def __post_init__(self) -> None:
        """Validate TurnResult fields after initialization.

        Raises:
            TypeError: If any field has an invalid type.
            ValueError: If role is empty or request_id is negative.
        """
        self._validate_non_empty_str(self.role, "role")
        self._validate_non_negative_int(self.request_id, "request_id")
        self._validate_str(self.assistant_text, "assistant_text")
        self._validate_str(self.delta_text, "delta_text")
        self._validate_str(self.full_items_text, "full_items_text")
        self._validate_non_negative_int(self.events_count, "events_count")
        self._validate_dict(self.last_event, "last_event")
        return None
