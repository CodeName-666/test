"""Event parsing utilities for Codex app-server streams."""
import json
from typing import Any, Dict, List, Optional


def safe_event_json(line: str) -> Optional[Dict[str, Any]]:
    """Parse a single JSON line emitted by the Codex app-server."""
    cleaned = (line or "").strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def normalize_item_type(item_type: Optional[str]) -> str:
    return (item_type or "").replace("_", "").lower()


def extract_text_from_item(obj: Dict[str, Any]) -> str:
    """Extract textual payloads from variant item structures."""
    text_value = obj.get("text")
    if isinstance(text_value, str) and text_value.strip():
        return text_value

    content = obj.get("content")
    if isinstance(content, list):
        parts: List[str] = []
        for entry in content:
            if isinstance(entry, dict) and entry.get("type") == "text" and isinstance(entry.get("text"), str):
                parts.append(entry["text"])
        if parts:
            return "".join(parts)

    summary = obj.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary

    return ""
