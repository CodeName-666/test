"""Apply file suggestions safely to the workspace."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

FILES_KEY = "files"

APPLIED_STATUS_WROTE = "WROTE"
APPLIED_STATUS_SKIPPED = "SKIPPED"
APPLIED_REASON_UNSAFE_PATH = "unsafe path"
APPLIED_REASON_INVALID_ENTRY = "invalid entry"
APPLIED_REASON_INVALID_PATH = "invalid path"
APPLIED_REASON_INVALID_CONTENT = "invalid content"
APPLIED_REASON_INVALID_FILES = "files is not a list"


class FileApplier:
    """Apply implementer file suggestions with safety checks.

    Designed for extension by overriding `_apply_implementer_files`,
    `_process_file_entry`, or `_is_safe_relative_path`.
    """

    def __init__(
        self,
        ensure_directory: Callable[[Path], None],
        write_text: Callable[[str, str], str],
    ) -> None:
        """Initialize the file applier.

        Args:
            ensure_directory: Callable that creates directories for target paths.
            write_text: Callable that writes run artifacts and returns the path.

        Returns:
            None.

        Raises:
            TypeError: If ensure_directory or write_text is not callable.
        """
        if callable(ensure_directory):
            self._ensure_directory = ensure_directory
        else:
            raise TypeError("ensure_directory must be callable")

        if callable(write_text):
            self._write_text = write_text
        else:
            raise TypeError("write_text must be callable")

    def _is_safe_relative_path(self, path_value: str) -> bool:
        """Return True when the path is relative and avoids parent traversal.

        Args:
            path_value: Path value to validate.

        Returns:
            True if the path is safe and relative, otherwise False.

        Raises:
            TypeError: If path_value is not a string.
            ValueError: If path_value is empty.
        """
        if isinstance(path_value, str):
            if path_value.strip():
                normalized_value = path_value.strip()
            else:
                raise ValueError("path_value must not be empty")
        else:
            raise TypeError("path_value must be a string")
        normalized_path = Path(normalized_value)
        result = not normalized_path.is_absolute() and ".." not in normalized_path.parts
        return result

    def _apply_implementer_files(
        self,
        reduced_payload: Dict[str, Any],
        turn_directory: str,
    ) -> None:
        """Apply implementer file suggestions safely to the workspace.

        Args:
            reduced_payload: Payload containing optional file change suggestions.
            turn_directory: Relative turn directory path for artifacts.

        Raises:
            TypeError: If inputs have invalid types.
            ValueError: If turn_directory is empty.
        """
        if isinstance(reduced_payload, dict):
            files_value = reduced_payload.get(FILES_KEY)
        else:
            raise TypeError("reduced_payload must be a dict")
        if isinstance(turn_directory, str):
            if turn_directory.strip():
                directory = turn_directory.strip()
            else:
                raise ValueError("turn_directory must not be empty")
        else:
            raise TypeError("turn_directory must be a string")

        applied: List[Dict[str, Any]] = []

        if files_value is None:
            applied = []
        elif isinstance(files_value, list):
            for entry in files_value:
                applied.append(self._process_file_entry(entry))
        else:
            applied.append(
                {"status": APPLIED_STATUS_SKIPPED, "reason": APPLIED_REASON_INVALID_FILES}
            )

        self._write_text(
            f"{directory}/applied_files.json",
            json.dumps(applied, ensure_ascii=False, indent=2),
        )

    def _process_file_entry(self, entry: Any) -> Dict[str, Any]:
        """Validate and apply a single file entry.

        Args:
            entry: File entry dictionary with path/content fields.

        Returns:
            Applied entry result dictionary.
        """
        result: Dict[str, Any]
        if isinstance(entry, dict):
            path_value = entry.get("path")
            content_value = entry.get("content")
            if not isinstance(path_value, str) or not path_value.strip():
                result = {
                    "status": APPLIED_STATUS_SKIPPED,
                    "reason": APPLIED_REASON_INVALID_PATH,
                }
            elif not isinstance(content_value, str):
                result = {
                    "path": path_value.strip(),
                    "status": APPLIED_STATUS_SKIPPED,
                    "reason": APPLIED_REASON_INVALID_CONTENT,
                }
            else:
                cleaned_path = path_value.strip()
                if not self._is_safe_relative_path(cleaned_path):
                    result = {
                        "path": cleaned_path,
                        "status": APPLIED_STATUS_SKIPPED,
                        "reason": APPLIED_REASON_UNSAFE_PATH,
                    }
                else:
                    target_path = Path(".") / cleaned_path
                    self._ensure_directory(target_path.parent)
                    try:
                        target_path.write_text(content_value, encoding="utf-8")
                        result = {
                            "path": cleaned_path,
                            "status": APPLIED_STATUS_WROTE,
                            "bytes": len(content_value.encode("utf-8")),
                        }
                    except Exception as exc:
                        result = {
                            "path": cleaned_path,
                            "status": APPLIED_STATUS_SKIPPED,
                            "reason": f"write failed: {exc}",
                        }
        else:
            result = {
                "status": APPLIED_STATUS_SKIPPED,
                "reason": APPLIED_REASON_INVALID_ENTRY,
            }
        return result
