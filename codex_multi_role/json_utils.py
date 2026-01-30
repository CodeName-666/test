"""JSON helpers tailored for parsing Codex assistant outputs."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


class JsonPayloadFormatter:
    """Extract and normalize JSON payloads from assistant text."""

    def __init__(self) -> None:
        # Remove code fences or inline backticks before scanning for JSON.
        import defaults

        self._code_fence_pattern = re.compile(defaults.DEFAULT_CODE_FENCE_PATTERN)

    def extract_first_json_object(self, text: str) -> Dict[str, Any]:
        """Extract the first top-level JSON object in a text blob."""
        normalized_text = (text or "").strip()
        normalized_text = self._code_fence_pattern.sub("", normalized_text)
        normalized_text = normalized_text.replace("`", "")

        start_index = normalized_text.find("{")
        if start_index == -1:
            raise ValueError("no '{' found")

        parsed_object: Optional[Dict[str, Any]] = None
        depth = 0
        for index in range(start_index, len(normalized_text)):
            character = normalized_text[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    parsed_object = json.loads(normalized_text[start_index : index + 1])
                    break

        if parsed_object is None:
            raise ValueError("no complete JSON object found")

        return parsed_object

    def parse_json_object_from_assistant_text(self, assistant_text: str) -> Dict[str, Any]:
        """Ensure the extracted JSON root is an object."""
        parsed_object = self.extract_first_json_object(assistant_text)
        if not isinstance(parsed_object, dict):
            raise ValueError("JSON root must be an object")
        return parsed_object

    def normalize_json(self, payload: Dict[str, Any]) -> str:
        """Pretty-print JSON for storing to disk."""
        normalized_json = json.dumps(payload, ensure_ascii=False, indent=2)
        return normalized_json
