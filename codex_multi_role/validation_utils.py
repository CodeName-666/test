"""Shared validation helpers for codex_multi_role types."""
from __future__ import annotations

from typing import Any, Optional, Type


class ValidationMixin:
    """Provide reusable validation helpers for model and client classes."""

    def _build_type_label(self, expected_type: Type[Any], type_label: Optional[str]) -> str:
        """Build a type label for error messages.

        Args:
            expected_type: Expected Python type for the value.
            type_label: Optional explicit label used in error messages.

        Returns:
            String label used in error messages.
        """
        label = ""
        if type_label is not None:
            label = type_label
        else:
            label = getattr(expected_type, "__name__", str(expected_type))
        return label

    def _validate_bool(self, value: Any, field_name: str) -> None:
        """Validate that a field is a boolean.

        Args:
            value: Value to validate.
            field_name: Field name used for error reporting.

        Raises:
            TypeError: If value is not a boolean.
        """
        if isinstance(value, bool):
            pass
        else:
            raise TypeError(f"{field_name} must be a boolean")
        return None

    def _validate_str(self, value: Any, field_name: str) -> None:
        """Validate that a field is a string.

        Args:
            value: Value to validate.
            field_name: Field name used for error reporting.

        Raises:
            TypeError: If value is not a string.
        """
        if isinstance(value, str):
            pass
        else:
            raise TypeError(f"{field_name} must be a string")
        return None

    def _validate_non_empty_str(self, value: Any, field_name: str) -> None:
        """Validate that a field is a non-empty string.

        Args:
            value: Value to validate.
            field_name: Field name used for error reporting.

        Raises:
            TypeError: If value is not a string.
            ValueError: If value is empty or whitespace.
        """
        if isinstance(value, str):
            if value.strip():
                pass
            else:
                raise ValueError(f"{field_name} must not be empty")
        else:
            raise TypeError(f"{field_name} must be a string")
        return None

    def _validate_optional_non_empty_str(
        self,
        value: Optional[str],
        field_name: str,
        empty_message: Optional[str] = None,
    ) -> None:
        """Validate that an optional string field is None or non-empty.

        Args:
            value: Value to validate.
            field_name: Field name used for error reporting.
            empty_message: Optional override for the empty-string error message.

        Raises:
            TypeError: If value is not a string or None.
            ValueError: If value is an empty string.
        """
        if value is None:
            pass
        elif isinstance(value, str):
            if value.strip():
                pass
            else:
                if empty_message is None:
                    raise ValueError(f"{field_name} must not be empty")
                else:
                    raise ValueError(empty_message)
        else:
            raise TypeError(f"{field_name} must be a string or None")
        return None

    def _validate_non_negative_int(self, value: Any, field_name: str) -> None:
        """Validate that a field is a non-negative integer.

        Args:
            value: Value to validate.
            field_name: Field name used for error reporting.

        Raises:
            TypeError: If value is not an integer.
            ValueError: If value is negative.
        """
        if isinstance(value, int):
            if value >= 0:
                pass
            else:
                raise ValueError(f"{field_name} must be zero or greater")
        else:
            raise TypeError(f"{field_name} must be an integer")
        return None

    def _validate_positive_int(self, value: Any, field_name: str) -> None:
        """Validate that a field is a positive integer.

        Args:
            value: Value to validate.
            field_name: Field name used for error reporting.

        Raises:
            TypeError: If value is not an integer.
            ValueError: If value is not greater than zero.
        """
        if isinstance(value, int):
            if value > 0:
                pass
            else:
                raise ValueError(f"{field_name} must be greater than zero")
        else:
            raise TypeError(f"{field_name} must be an integer")
        return None

    def _validate_dict(self, value: Any, field_name: str) -> None:
        """Validate that a field is a dictionary.

        Args:
            value: Value to validate.
            field_name: Field name used for error reporting.

        Raises:
            TypeError: If value is not a dict.
        """
        if isinstance(value, dict):
            pass
        else:
            raise TypeError(f"{field_name} must be a dict")
        return None

    def _validate_instance(
        self,
        value: Any,
        expected_type: Type[Any],
        field_name: str,
        type_label: Optional[str] = None,
    ) -> None:
        """Validate that a value is an instance of the expected type.

        Args:
            value: Value to validate.
            expected_type: Expected Python type for the value.
            field_name: Field name used for error reporting.
            type_label: Optional explicit label for error messages.

        Raises:
            TypeError: If value is not an instance of expected_type.
        """
        if isinstance(value, expected_type):
            pass
        else:
            label = self._build_type_label(expected_type, type_label)
            raise TypeError(f"{field_name} must be a {label} instance")
        return None

    def _validate_optional_instance(
        self,
        value: Optional[Any],
        expected_type: Type[Any],
        field_name: str,
        type_label: Optional[str] = None,
    ) -> None:
        """Validate that a value is None or an instance of the expected type.

        Args:
            value: Value to validate.
            expected_type: Expected Python type for the value.
            field_name: Field name used for error reporting.
            type_label: Optional explicit label for error messages.

        Raises:
            TypeError: If value is not None and not an instance of expected_type.
        """
        if value is None:
            pass
        elif isinstance(value, expected_type):
            pass
        else:
            label = self._build_type_label(expected_type, type_label)
            raise TypeError(f"{field_name} must be a {label} or None")
        return None
