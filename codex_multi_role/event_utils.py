"""Event parsing utilities for Codex app-server streams."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class EventParser:
    """Normalize and extract text from the Codex app-server event stream."""

    def parse_event_json_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parse a single JSON line emitted by the Codex app-server.

        Args:
            line: Raw line from the event stream.

        Returns:
            Parsed JSON object if the line contains valid JSON, otherwise None.

        Raises:
            TypeError: If line is not a string.
        """
        if isinstance(line, str):
            cleaned_line = line.strip()
        else:
            raise TypeError("line must be a string")

        parsed_message: Optional[Dict[str, Any]] = None
        if cleaned_line:
            try:
                parsed_message = json.loads(cleaned_line)
            except json.JSONDecodeError:
                parsed_message = None
        else:
            parsed_message = None
        return parsed_message

    def normalize_item_type_name(self, item_type: Optional[str]) -> str:
        """Normalize item type labels for consistent comparisons.

        Args:
            item_type: Raw item type label or None.

        Returns:
            Normalized, lowercase item type label.

        Raises:
            TypeError: If item_type is not a string or None.
        """
        normalized_type = ""
        if item_type is None:
            normalized_type = ""
        elif isinstance(item_type, str):
            normalized_type = item_type.replace("_", "").lower()
        else:
            raise TypeError("item_type must be a string or None")
        return normalized_type

    def extract_text_from_item(self, item_payload: Dict[str, Any]) -> str:
        """Extract textual payloads from variant item structures.

        Args:
            item_payload: Item dictionary emitted by the event stream.

        Returns:
            Extracted text content or an empty string when none is present.

        Raises:
            TypeError: If item_payload is not a mapping.
        """
        if isinstance(item_payload, dict):
            payload = item_payload
        else:
            raise TypeError("item_payload must be a mapping")

        extracted_text = ""
        text_value = payload.get("text")
        if isinstance(text_value, str) and text_value.strip():
            extracted_text = text_value
        else:
            content = payload.get("content")
            if isinstance(content, list):
                parts: List[str] = []
                for entry in content:
                    if (
                        isinstance(entry, dict)
                        and entry.get("type") == "text"
                        and isinstance(entry.get("text"), str)
                    ):
                        parts.append(entry["text"])
                if parts:
                    extracted_text = "".join(parts)
            if not extracted_text:
                summary = payload.get("summary")
                if isinstance(summary, str) and summary.strip():
                    extracted_text = summary

        return extracted_text
