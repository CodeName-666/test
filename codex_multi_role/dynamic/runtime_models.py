"""Runtime contracts for planner-gated dynamic orchestration.

This module defines normalized runtime structures used by the dynamic
orchestrator:
- Context packets sent to delegated agents
- Worker output normalization and validation
- Deterministic question identifiers
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


COMPLETED_STATUS = "completed"
BLOCKED_STATUS = "blocked"
FAILED_STATUS = "failed"
ALLOWED_WORKER_STATUSES = {COMPLETED_STATUS, BLOCKED_STATUS, FAILED_STATUS}


def build_question_id(question_text: str, source: str) -> str:
    """Build a deterministic question identifier.

    Args:
        question_text: Human-readable question content.
        source: Stable question source identifier.

    Returns:
        Stable question id derived from source and text content.
    """
    normalized = f"{source.strip()}::{question_text.strip()}".encode("utf-8")
    digest = hashlib.sha256(normalized).hexdigest()
    result = digest[:16]
    return result


@dataclass(frozen=True)
class DetailIndexEntry:
    """Reference entry for lazily loadable details in a context packet.

    Attributes:
        id: Stable detail identifier.
        title: Human-readable detail title.
        summary: Short detail summary.
        tags: Optional detail tags for filtering.
    """

    id: str
    title: str
    summary: str
    tags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate detail index entry fields after initialization.

        Raises:
            TypeError: If field types are invalid.
            ValueError: If required string fields are empty.
        """
        if not isinstance(self.id, str):
            raise TypeError("id must be a string")
        if not self.id.strip():
            raise ValueError("id must not be empty")
        if not isinstance(self.title, str):
            raise TypeError("title must be a string")
        if not self.title.strip():
            raise ValueError("title must not be empty")
        if not isinstance(self.summary, str):
            raise TypeError("summary must be a string")
        if not isinstance(self.tags, list):
            raise TypeError("tags must be a list")
        for index, tag in enumerate(self.tags):
            if not isinstance(tag, str):
                raise TypeError(f"tags[{index}] must be a string")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the detail entry to a plain dictionary.

        Returns:
            JSON-serializable dictionary representation.
        """
        result = {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "tags": list(self.tags),
        }
        return result


@dataclass
class ContextPacket:
    """Curated context package for delegated agents.

    Attributes:
        planner_compact: Path or identifier to planner compact truth document.
        detail_index: Indexed detail descriptors available for lazy loading.
        answered_questions: Previously answered questions.
        active_assumptions: Active assumptions currently used by planner.
    """

    planner_compact: str
    detail_index: List[DetailIndexEntry] = field(default_factory=list)
    answered_questions: List[Dict[str, Any]] = field(default_factory=list)
    active_assumptions: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate context packet fields after initialization.

        Raises:
            TypeError: If field types are invalid.
            ValueError: If planner_compact is empty.
        """
        if not isinstance(self.planner_compact, str):
            raise TypeError("planner_compact must be a string")
        if not self.planner_compact.strip():
            raise ValueError("planner_compact must not be empty")
        if not isinstance(self.detail_index, list):
            raise TypeError("detail_index must be a list")
        if not isinstance(self.answered_questions, list):
            raise TypeError("answered_questions must be a list")
        if not isinstance(self.active_assumptions, list):
            raise TypeError("active_assumptions must be a list")
        for index, assumption in enumerate(self.active_assumptions):
            if not isinstance(assumption, str):
                raise TypeError(
                    f"active_assumptions[{index}] must be a string"
                )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize context packet to a plain dictionary.

        Returns:
            JSON-serializable dictionary representation.
        """
        result = {
            "planner_compact": self.planner_compact,
            "detail_index": [entry.to_dict() for entry in self.detail_index],
            "answered_questions": list(self.answered_questions),
            "active_assumptions": list(self.active_assumptions),
        }
        return result


@dataclass
class WorkerOutput:
    """Normalized worker output structure.

    Attributes:
        status: Worker status (`completed`, `blocked`, `failed`).
        compact_md: Compact markdown summary.
        detailed_md: Detailed markdown report.
        blocking_questions: Blocking questions that prevent completion.
        optional_questions: Optional questions that can improve quality.
        missing_info_requests: Missing information requests for planner.
        assumptions_made: Assumptions used by the worker.
        coverage: Acceptance-criteria coverage report.
        side_effect_log: Optional tool side-effect records from worker.
        notes: Non-fatal validator notes, such as truncation warnings.
        raw_payload: Raw worker payload after normalization.
    """

    status: str
    compact_md: str
    detailed_md: str
    blocking_questions: List[Dict[str, Any]] = field(default_factory=list)
    optional_questions: List[Dict[str, Any]] = field(default_factory=list)
    missing_info_requests: List[str] = field(default_factory=list)
    assumptions_made: List[str] = field(default_factory=list)
    coverage: Dict[str, List[str]] = field(
        default_factory=lambda: {"criteria_met": [], "criteria_unmet": []}
    )
    side_effect_log: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize normalized worker output.

        Returns:
            JSON-serializable output dictionary.
        """
        result = {
            "status": self.status,
            "compact_md": self.compact_md,
            "detailed_md": self.detailed_md,
            "blocking_questions": list(self.blocking_questions),
            "optional_questions": list(self.optional_questions),
            "missing_info_requests": list(self.missing_info_requests),
            "assumptions_made": list(self.assumptions_made),
            "coverage": {
                "criteria_met": list(self.coverage.get("criteria_met", [])),
                "criteria_unmet": list(self.coverage.get("criteria_unmet", [])),
            },
            "side_effect_log": list(self.side_effect_log),
            "notes": list(self.notes),
        }
        return result


@dataclass
class WorkerOutputValidation:
    """Validation result wrapper for worker output payloads.

    Attributes:
        worker_output: Normalized worker output when validation succeeded.
        fatal_errors: Errors that invalidate output.
        non_fatal_errors: Recoverable issues fixed during normalization.
    """

    worker_output: Optional[WorkerOutput]
    fatal_errors: List[str] = field(default_factory=list)
    non_fatal_errors: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Check whether validation produced a usable worker output."""
        result = self.worker_output is not None and not self.fatal_errors
        return result


class WorkerOutputValidator:
    """Normalize and validate worker payloads for planner-gated orchestration."""

    def __init__(
        self,
        max_compact_md_chars: int = 24_000,
        max_detailed_md_chars: int = 80_000,
    ) -> None:
        """Initialize validator length limits.

        Args:
            max_compact_md_chars: Maximum compact markdown length.
            max_detailed_md_chars: Maximum detailed markdown length.

        Raises:
            ValueError: If a limit is not positive.
        """
        if max_compact_md_chars <= 0:
            raise ValueError("max_compact_md_chars must be greater than zero")
        if max_detailed_md_chars <= 0:
            raise ValueError("max_detailed_md_chars must be greater than zero")
        self._max_compact_md_chars = max_compact_md_chars
        self._max_detailed_md_chars = max_detailed_md_chars

    def validate(self, payload: Dict[str, Any]) -> WorkerOutputValidation:
        """Validate and normalize a worker payload.

        Args:
            payload: Raw worker payload dictionary.

        Returns:
            Validation result with normalized output and issues.
        """
        fatal_errors: List[str] = []
        non_fatal_errors: List[str] = []
        worker_output: Optional[WorkerOutput] = None

        if not isinstance(payload, dict):
            fatal_errors.append("worker payload must be a JSON object")
        else:
            status = self._normalize_status(payload, non_fatal_errors)
            compact_md = self._normalize_markdown_field(
                payload,
                key="compact_md",
                fallback_keys=["summary"],
                max_chars=self._max_compact_md_chars,
                field_label="compact_md",
                non_fatal_errors=non_fatal_errors,
            )
            detailed_md = self._normalize_markdown_field(
                payload,
                key="detailed_md",
                fallback_keys=["analysis_md", "details_md"],
                max_chars=self._max_detailed_md_chars,
                field_label="detailed_md",
                non_fatal_errors=non_fatal_errors,
            )
            blocking_questions = self._normalize_question_list(
                payload.get("blocking_questions", []),
                source_suffix="blocking",
                non_fatal_errors=non_fatal_errors,
            )
            optional_questions = self._normalize_question_list(
                payload.get("optional_questions", payload.get("questions", [])),
                source_suffix="optional",
                non_fatal_errors=non_fatal_errors,
            )
            missing_info_requests = self._normalize_string_list(
                payload.get("missing_info_requests", payload.get("blockers", [])),
                "missing_info_requests",
                non_fatal_errors,
            )
            assumptions_made = self._normalize_string_list(
                payload.get("assumptions_made", []),
                "assumptions_made",
                non_fatal_errors,
            )
            coverage = self._normalize_coverage(payload.get("coverage", {}), non_fatal_errors)
            side_effect_log = self._normalize_side_effect_log(
                payload.get("side_effect_log", []), non_fatal_errors
            )
            self._validate_status_question_constraints(
                status,
                blocking_questions,
                fatal_errors,
            )
            if status == FAILED_STATUS and not detailed_md.strip():
                error_value = payload.get("error")
                if isinstance(error_value, str) and error_value.strip():
                    detailed_md = error_value.strip()
                else:
                    non_fatal_errors.append(
                        "failed output without detailed_md/error; set detailed_md to empty string"
                    )
            if not fatal_errors:
                worker_output = WorkerOutput(
                    status=status,
                    compact_md=compact_md,
                    detailed_md=detailed_md,
                    blocking_questions=blocking_questions,
                    optional_questions=optional_questions,
                    missing_info_requests=missing_info_requests,
                    assumptions_made=assumptions_made,
                    coverage=coverage,
                    side_effect_log=side_effect_log,
                    notes=list(non_fatal_errors),
                    raw_payload=dict(payload),
                )

        result = WorkerOutputValidation(
            worker_output=worker_output,
            fatal_errors=fatal_errors,
            non_fatal_errors=non_fatal_errors,
        )
        return result

    def _normalize_status(
        self,
        payload: Dict[str, Any],
        non_fatal_errors: List[str],
    ) -> str:
        status_value = payload.get("status")
        normalized_status = FAILED_STATUS
        if isinstance(status_value, str):
            candidate = status_value.strip().lower()
            if candidate in ALLOWED_WORKER_STATUSES:
                normalized_status = candidate
            else:
                non_fatal_errors.append(
                    f"unknown status '{status_value}', defaulted to '{FAILED_STATUS}'"
                )
        elif payload.get("error") is not None:
            normalized_status = FAILED_STATUS
        elif payload.get("blocking_questions"):
            normalized_status = BLOCKED_STATUS
        else:
            normalized_status = COMPLETED_STATUS
        return normalized_status

    def _normalize_markdown_field(
        self,
        payload: Dict[str, Any],
        key: str,
        fallback_keys: List[str],
        max_chars: int,
        field_label: str,
        non_fatal_errors: List[str],
    ) -> str:
        value = payload.get(key)
        normalized_text = ""
        if isinstance(value, str):
            normalized_text = value
        elif value is None:
            normalized_text = ""
        else:
            non_fatal_errors.append(
                f"{field_label} has invalid type {type(value).__name__}; coerced to string"
            )
            normalized_text = str(value)
        if not normalized_text:
            for fallback_key in fallback_keys:
                fallback_value = payload.get(fallback_key)
                if isinstance(fallback_value, str) and fallback_value:
                    normalized_text = fallback_value
                    break
        if len(normalized_text) > max_chars:
            non_fatal_errors.append(
                f"{field_label} truncated to {max_chars} chars"
            )
            normalized_text = normalized_text[:max_chars]
        return normalized_text

    def _normalize_string_list(
        self,
        value: Any,
        field_name: str,
        non_fatal_errors: List[str],
    ) -> List[str]:
        normalized: List[str] = []
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, str):
                    normalized.append(item)
                else:
                    non_fatal_errors.append(
                        f"{field_name}[{index}] coerced to string"
                    )
                    normalized.append(str(item))
        elif value is None:
            normalized = []
        else:
            non_fatal_errors.append(
                f"{field_name} has invalid type {type(value).__name__}; wrapped as string"
            )
            normalized = [str(value)]
        return normalized

    def _normalize_question_list(
        self,
        value: Any,
        source_suffix: str,
        non_fatal_errors: List[str],
    ) -> List[Dict[str, Any]]:
        normalized_questions: List[Dict[str, Any]] = []
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    text_value = item.get("question", "")
                    if isinstance(text_value, str):
                        question_text = text_value.strip()
                    else:
                        question_text = str(text_value).strip()
                    if question_text:
                        source_value = item.get("source", f"{source_suffix}_{index}")
                        if isinstance(source_value, str):
                            question_source = source_value
                        else:
                            question_source = str(source_value)
                        if not question_source.strip():
                            question_source = f"{source_suffix}_{index}"
                        normalized_id = build_question_id(
                            question_text=question_text,
                            source=question_source,
                        )
                        normalized_questions.append(
                            {
                                "question_id": normalized_id,
                                "question": question_text,
                                "source": question_source,
                                "priority": item.get("priority", "normal"),
                                "expected_answer_format": item.get(
                                    "expected_answer_format", "text"
                                ),
                            }
                        )
                    else:
                        non_fatal_errors.append(
                            f"{source_suffix}[{index}] dropped because question text was empty"
                        )
                else:
                    question_text = str(item).strip()
                    if question_text:
                        normalized_questions.append(
                            {
                                "question_id": build_question_id(
                                    question_text=question_text,
                                    source=f"{source_suffix}_{index}",
                                ),
                                "question": question_text,
                                "source": f"{source_suffix}_{index}",
                                "priority": "normal",
                                "expected_answer_format": "text",
                            }
                        )
                    else:
                        non_fatal_errors.append(
                            f"{source_suffix}[{index}] dropped because it was empty"
                        )
        elif value is not None:
            non_fatal_errors.append(
                f"{source_suffix} has invalid type {type(value).__name__}; ignored"
            )
        return normalized_questions

    def _normalize_coverage(
        self,
        value: Any,
        non_fatal_errors: List[str],
    ) -> Dict[str, List[str]]:
        coverage = {"criteria_met": [], "criteria_unmet": []}
        if isinstance(value, dict):
            coverage["criteria_met"] = self._normalize_string_list(
                value.get("criteria_met", []),
                "coverage.criteria_met",
                non_fatal_errors,
            )
            coverage["criteria_unmet"] = self._normalize_string_list(
                value.get("criteria_unmet", []),
                "coverage.criteria_unmet",
                non_fatal_errors,
            )
        else:
            non_fatal_errors.append("coverage missing or invalid; initialized empty")
        return coverage

    def _normalize_side_effect_log(
        self,
        value: Any,
        non_fatal_errors: List[str],
    ) -> List[Dict[str, Any]]:
        side_effects: List[Dict[str, Any]] = []
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    side_effects.append(item)
                else:
                    non_fatal_errors.append(
                        f"side_effect_log[{index}] ignored because it is not an object"
                    )
        elif value is not None:
            non_fatal_errors.append(
                f"side_effect_log has invalid type {type(value).__name__}; ignored"
            )
        return side_effects

    def _validate_status_question_constraints(
        self,
        status: str,
        blocking_questions: List[Dict[str, Any]],
        fatal_errors: List[str],
    ) -> None:
        if status == BLOCKED_STATUS and not blocking_questions:
            fatal_errors.append(
                "blocked output must include at least one blocking question"
            )
        if status == COMPLETED_STATUS and blocking_questions:
            fatal_errors.append(
                "completed output must not include blocking questions"
            )
