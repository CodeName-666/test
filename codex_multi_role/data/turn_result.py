"""Data structure that holds the result of a Codex turn."""
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class TurnResult:
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
        self._validate_role()
        self._validate_request_id()
        self._validate_text_fields()
        self._validate_events_count()
        self._validate_last_event()
        return None

    def _validate_role(self) -> None:
        """Validate the role field."""
        if isinstance(self.role, str):
            if self.role.strip():
                pass
            else:
                raise ValueError("role must not be empty")
        else:
            raise TypeError("role must be a string")
        return None

    def _validate_request_id(self) -> None:
        """Validate the request_id field."""
        if isinstance(self.request_id, int):
            if self.request_id >= 0:
                pass
            else:
                raise ValueError("request_id must be zero or greater")
        else:
            raise TypeError("request_id must be an integer")
        return None

    def _validate_text_fields(self) -> None:
        """Validate text fields for correct types."""
        if isinstance(self.assistant_text, str):
            pass
        else:
            raise TypeError("assistant_text must be a string")

        if isinstance(self.delta_text, str):
            pass
        else:
            raise TypeError("delta_text must be a string")

        if isinstance(self.full_items_text, str):
            pass
        else:
            raise TypeError("full_items_text must be a string")
        return None

    def _validate_events_count(self) -> None:
        """Validate the events_count field."""
        if isinstance(self.events_count, int):
            if self.events_count >= 0:
                pass
            else:
                raise ValueError("events_count must be zero or greater")
        else:
            raise TypeError("events_count must be an integer")
        return None

    def _validate_last_event(self) -> None:
        """Validate the last_event field."""
        if isinstance(self.last_event, dict):
            pass
        else:
            raise TypeError("last_event must be a dict")
        return None
