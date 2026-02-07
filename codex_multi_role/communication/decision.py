"""Planner decision contracts for communication orchestration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .contracts import build_question_id
from .interaction import Question


@dataclass
class PlannerDecision:
    """Decision payload returned by planner.

    Attributes:
        summary: Short decision summary.
        needs_user_input: Whether planner requests user input before continuing.
        questions: Planner questions for the user.
        delegations: Delegation specifications for worker agents.
        action: Decision action (`delegate`, `ask_user`, or `done`).
        status: Planner status (`CONTINUE` or `DONE`).
        planner_decision: Structured planner I/O status block.
        wave_compact_md: Optional planner compact wave markdown.
        wave_detailed_md: Optional planner detailed wave markdown.
        raw_payload: Raw planner payload.
    """

    summary: str = ""
    needs_user_input: bool = False
    questions: List[Question] = field(default_factory=list)
    delegations: List[Dict[str, Any]] = field(default_factory=list)
    action: str = "delegate"
    status: str = "CONTINUE"
    planner_decision: Dict[str, Any] = field(default_factory=dict)
    wave_compact_md: str = ""
    wave_detailed_md: str = ""
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "PlannerDecision":
        """Create decision from planner payload.

        Args:
            payload: Parsed planner payload.

        Returns:
            Normalized planner decision object.
        """
        questions = cls._parse_questions(payload)
        planner_decision_value = cls._normalize_dict(payload.get("planner_decision"))
        delegations_value = cls._normalize_list(payload.get("delegations"))
        wave_compact_md = cls._normalize_str(payload.get("wave_compact_md"))
        wave_detailed_md = cls._normalize_str(payload.get("wave_detailed_md"))
        summary_value = cls._normalize_str(payload.get("summary"))
        action_value = cls._normalize_str(payload.get("action"), default="delegate")
        status_value = cls._normalize_str(payload.get("status"), default="CONTINUE")
        needs_user_input = bool(payload.get("needs_user_input", False))
        result = cls(
            summary=summary_value,
            needs_user_input=needs_user_input,
            questions=questions,
            delegations=delegations_value,
            action=action_value,
            status=status_value,
            planner_decision=planner_decision_value,
            wave_compact_md=wave_compact_md,
            wave_detailed_md=wave_detailed_md,
            raw_payload=payload,
        )
        return result

    @classmethod
    def _parse_questions(cls, payload: Dict[str, Any]) -> List[Question]:
        questions: List[Question] = []
        raw_questions = payload.get("questions", [])
        if isinstance(raw_questions, list):
            for index, raw_question in enumerate(raw_questions):
                question = cls._build_question(raw_question, index)
                if question is not None:
                    questions.append(question)
        return questions

    @staticmethod
    def _normalize_dict(value: Any) -> Dict[str, Any]:
        result: Dict[str, Any]
        if isinstance(value, dict):
            result = value
        else:
            result = {}
        return result

    @staticmethod
    def _normalize_list(value: Any) -> List[Any]:
        result: List[Any]
        if isinstance(value, list):
            result = value
        else:
            result = []
        return result

    @staticmethod
    def _normalize_str(value: Any, default: str = "") -> str:
        result: str
        if isinstance(value, str):
            result = value
        elif value is None:
            result = default
        else:
            result = str(value)
        return result

    @staticmethod
    def _build_question(raw_question: Any, index: int) -> Optional[Question]:
        question: Optional[Question] = None
        if isinstance(raw_question, dict):
            question_text = raw_question.get("question", "")
            if isinstance(question_text, str) and question_text.strip():
                source = raw_question.get("source", f"planner_{index}")
                if not isinstance(source, str):
                    source = f"planner_{index}"
                if not source.strip():
                    source = f"planner_{index}"
                normalized_id = build_question_id(question_text, source)
                category = raw_question.get("category", "optional")
                if not isinstance(category, str):
                    category = "optional"
                default_suggestion = raw_question.get("default_suggestion")
                if default_suggestion is not None and not isinstance(default_suggestion, str):
                    default_suggestion = str(default_suggestion)
                context = raw_question.get("context")
                if context is not None and not isinstance(context, str):
                    context = str(context)
                priority = raw_question.get("priority", "normal")
                if not isinstance(priority, str):
                    priority = "normal"
                expected_format = raw_question.get("expected_answer_format", "text")
                if not isinstance(expected_format, str):
                    expected_format = "text"
                question = Question(
                    id=normalized_id,
                    question=question_text,
                    category=category,
                    default_suggestion=default_suggestion,
                    context=context,
                    priority=priority,
                    expected_answer_format=expected_format,
                )
        return question

    @property
    def is_done(self) -> bool:
        """Check if planner signaled completion."""
        status_done = self.status.strip().upper() == "DONE"
        action_done = self.action.strip().lower() == "done"
        result = status_done or action_done
        return result


__all__ = ["PlannerDecision"]
