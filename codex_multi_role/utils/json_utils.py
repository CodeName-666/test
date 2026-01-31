"""JSON helpers tailored for parsing Codex assistant outputs."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


class JsonPayloadFormatter:
    """Extract and normalize JSON payloads from assistant text."""

    def __init__(self) -> None:
        """Initialize the formatter with the configured code-fence pattern.

        Raises:
            TypeError: If the configured code fence pattern is not a string.
            ValueError: If the configured code fence pattern is empty.
        """
        import defaults

        pattern_value = defaults.DEFAULT_CODE_FENCE_PATTERN
        if isinstance(pattern_value, str):
            if pattern_value.strip():
                compiled = re.compile(pattern_value)
            else:
                raise ValueError("DEFAULT_CODE_FENCE_PATTERN must not be empty")
        else:
            raise TypeError("DEFAULT_CODE_FENCE_PATTERN must be a string")
        self._code_fence_pattern = compiled
        return None

    def extract_first_json_object(self, text: str) -> Dict[str, Any]:
        """Extract the first top-level JSON object in a text blob.

        Args:
            text: Text that should contain a JSON object.

        Returns:
            Parsed JSON object as a dictionary.

        Raises:
            TypeError: If text is not a string.
            ValueError: If no JSON object can be parsed.
            json.JSONDecodeError: If parsing fails on a candidate JSON segment.
        """
        if isinstance(text, str):
            normalized_text = text.strip()
        else:
            raise TypeError("text must be a string")
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
        """Ensure the extracted JSON root is an object.

        Args:
            assistant_text: Assistant output that should contain a JSON object.

        Returns:
            Parsed JSON object as a dictionary.

        Raises:
            TypeError: If assistant_text is not a string.
            ValueError: If the JSON root is not an object.
            json.JSONDecodeError: If parsing fails on a candidate JSON segment.
        """
        parsed_object = self.extract_first_json_object(assistant_text)
        if isinstance(parsed_object, dict):
            result = parsed_object
        else:
            raise ValueError("JSON root must be an object")
        return result

    def normalize_json(self, payload: Dict[str, Any]) -> str:
        """Pretty-print JSON for storing to disk.

        Args:
            payload: JSON-compatible mapping to serialize.

        Returns:
            Pretty-printed JSON string.

        Raises:
            TypeError: If payload is not a dictionary.
        """
        if isinstance(payload, dict):
            normalized_json = json.dumps(payload, ensure_ascii=False, indent=2)
        else:
            raise TypeError("payload must be a dict")
        return normalized_json
