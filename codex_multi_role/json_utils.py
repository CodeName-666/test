"""JSON helpers tailored for parsing Codex assistant outputs."""
import json
import re
from typing import Any, Dict


def extract_first_json_object(text: str) -> Dict[str, Any]:
    """Extract the first top-level JSON object in a text blob."""
    text = (text or "").strip()
    text = re.sub(r"`(?:json)?\s*", "", text)
    text = text.replace("`", "")

    start = text.find("{")
    if start == -1:
        raise ValueError("no '{' found")

    depth = 0
    for idx in range(start, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : idx + 1])

    raise ValueError("no complete JSON object found")


def parse_json_object_from_assistant_text(assistant_text: str) -> Dict[str, Any]:
    obj = extract_first_json_object(assistant_text)
    if not isinstance(obj, dict):
        raise ValueError("JSON root must be an object")
    return obj


def normalize_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
