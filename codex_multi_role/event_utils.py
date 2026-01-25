"""Event parsing utilities for Codex app-server streams."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class EventParser:
    """Normalize and extract text from the Codex app-server event stream."""

    def parse_event_json_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parse a single JSON line emitted by the Codex app-server."""
        cleaned_line = (line or "").strip()
        parsed_message: Optional[Dict[str, Any]] = None
        if cleaned_line:
            try:
                parsed_message = json.loads(cleaned_line)
            except json.JSONDecodeError:
                parsed_message = None
        return parsed_message

    def normalize_item_type_name(self, item_type: Optional[str]) -> str:
        """Normalize item type labels for consistent comparisons."""
        normalized_type = (item_type or "").replace("_", "").lower()
        return normalized_type

    def extract_text_from_item(self, item_payload: Dict[str, Any]) -> str:
        """Extract textual payloads from variant item structures."""
        extracted_text = ""

        text_value = item_payload.get("text")
        if isinstance(text_value, str) and text_value.strip():
            extracted_text = text_value
        else:
            content = item_payload.get("content")
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
                summary = item_payload.get("summary")
                if isinstance(summary, str) and summary.strip():
                    extracted_text = summary

        return extracted_text


DEFAULT_EVENT_PARSER = EventParser()
