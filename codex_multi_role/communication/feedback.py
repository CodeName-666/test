"""Feedback normalization and tracking for planner-gated orchestration."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .contracts import WorkerOutput, WorkerOutputValidator
from .interaction import Answer, Question, UserInteraction


class FeedbackStatus(Enum):
    """Status of normalized agent feedback."""

    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class AgentFeedback:
    """Normalized feedback record consumed by the planner.

    Attributes:
        agent: Name of the agent that produced this feedback.
        delegation_id: ID of the delegation this feedback refers to.
        status: Normalized feedback status.
        result: Raw payload from the worker.
        worker_output: Normalized worker output when validation succeeds.
        clarification_questions: Questions produced by the worker.
        blockers: Blocking reasons from worker output.
        error: Error message for failed feedback.
        validation_errors: Fatal validator errors if any.
    """

    agent: str
    delegation_id: str
    status: FeedbackStatus
    result: Dict[str, Any] = field(default_factory=dict)
    worker_output: Optional[WorkerOutput] = None
    clarification_questions: List[Question] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    error: Optional[str] = None
    validation_errors: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate feedback fields after initialization."""
        if not self.agent or not isinstance(self.agent, str):
            raise ValueError("agent must be a non-empty string")
        if not self.delegation_id or not isinstance(self.delegation_id, str):
            raise ValueError("delegation_id must be a non-empty string")
        if isinstance(self.status, str):
            object.__setattr__(self, "status", FeedbackStatus(self.status))

    @property
    def needs_clarification(self) -> bool:
        """Check if this feedback includes planner-visible questions."""
        result = self.status in (
            FeedbackStatus.NEEDS_CLARIFICATION,
            FeedbackStatus.BLOCKED,
        )
        return result

    @property
    def is_blocked(self) -> bool:
        """Check if feedback is blocked."""
        result = self.status == FeedbackStatus.BLOCKED
        return result

    @property
    def is_successful(self) -> bool:
        """Check if work completed successfully without follow-up."""
        result = self.status == FeedbackStatus.COMPLETED
        return result


class FeedbackLoop:
    """Normalize worker payloads and track feedback history for planner context."""

    def __init__(
        self,
        user_interaction: UserInteraction,
        worker_output_validator: Optional[WorkerOutputValidator] = None,
    ) -> None:
        """Initialize feedback loop.

        Args:
            user_interaction: Interface for optional user interaction.
            worker_output_validator: Optional validator override.
        """
        self._user_interaction = user_interaction
        self._worker_output_validator = worker_output_validator or WorkerOutputValidator()
        self._feedback_history: List[AgentFeedback] = []

    def process_agent_result(
        self,
        agent: str,
        delegation_id: str,
        result: Dict[str, Any],
    ) -> AgentFeedback:
        """Normalize one worker result into structured feedback.

        Args:
            agent: Worker agent id.
            delegation_id: Delegation id.
            result: Raw worker payload.

        Returns:
            Structured feedback entry.
        """
        validation = self._worker_output_validator.validate(result)
        questions: List[Question] = []
        blockers: List[str] = []
        status = FeedbackStatus.FAILED
        error_message: Optional[str] = None
        worker_output = validation.worker_output

        if validation.is_valid and worker_output is not None:
            blocking_questions = [
                self._question_from_dict(question_payload, "critical")
                for question_payload in worker_output.blocking_questions
            ]
            optional_questions = [
                self._question_from_dict(question_payload, "optional")
                for question_payload in worker_output.optional_questions
            ]
            questions = blocking_questions + optional_questions
            blockers = list(worker_output.missing_info_requests)
            if worker_output.status == "failed":
                status = FeedbackStatus.FAILED
                error_message = result.get("error") if isinstance(result.get("error"), str) else None
            elif worker_output.status == "blocked":
                status = FeedbackStatus.BLOCKED
            elif optional_questions:
                status = FeedbackStatus.NEEDS_CLARIFICATION
            else:
                status = FeedbackStatus.COMPLETED
        else:
            status = FeedbackStatus.FAILED
            error_message = "; ".join(validation.fatal_errors) or "invalid worker output"

        feedback = AgentFeedback(
            agent=agent,
            delegation_id=delegation_id,
            status=status,
            result=result,
            worker_output=worker_output,
            clarification_questions=questions,
            blockers=blockers,
            error=error_message,
            validation_errors=list(validation.fatal_errors),
        )
        self._feedback_history.append(feedback)
        return feedback

    def _question_from_dict(self, payload: Dict[str, Any], category: str) -> Question:
        question_id = payload.get("question_id")
        question_text = payload.get("question")
        source = payload.get("source")
        if not isinstance(question_id, str) or not question_id.strip():
            question_id = f"{category}_{len(self._feedback_history)}"
        if not isinstance(question_text, str) or not question_text.strip():
            question_text = "No question text provided."
        if not isinstance(source, str):
            source = "worker"
        question = Question(
            id=question_id,
            question=question_text,
            category=category,
            context=source,
            default_suggestion=None,
            priority=payload.get("priority", "normal"),
            expected_answer_format=payload.get("expected_answer_format", "text"),
        )
        return question

    def get_pending_clarifications(
        self,
        feedbacks: List[AgentFeedback],
    ) -> List[Question]:
        """Collect unresolved questions for planner context."""
        questions: List[Question] = []
        for feedback in feedbacks:
            if feedback.needs_clarification:
                questions.extend(feedback.clarification_questions)
        return questions

    def route_clarifications_to_user(
        self,
        feedbacks: List[AgentFeedback],
    ) -> Dict[str, Answer]:
        """Ask user questions that were explicitly escalated by planner.

        Args:
            feedbacks: Feedback entries containing clarification questions.

        Returns:
            Mapping from question id to answers.
        """
        answer_map: Dict[str, Answer]
        questions = self.get_pending_clarifications(feedbacks)
        if questions:
            critical_questions = [
                question for question in questions if question.category == "critical"
            ]
            if critical_questions:
                to_ask = critical_questions
            else:
                to_ask = questions
            answers = self._user_interaction.ask_questions(to_ask)
            answer_map = {answer.question_id: answer for answer in answers}
        else:
            answer_map = {}
        return answer_map

    def build_clarification_context(
        self,
        answers: Dict[str, Answer],
        delegation_id: str,
    ) -> Dict[str, Any]:
        """Build clarification context for planner-agent follow-up.

        Args:
            answers: Mapping from question id to answer.
            delegation_id: Delegation id for metadata.

        Returns:
            Context dictionary containing all provided clarifications.
        """
        if not isinstance(delegation_id, str):
            raise TypeError("delegation_id must be a string")
        context = {
            "delegation_id": delegation_id,
            "clarifications": {question_id: answer.answer for question_id, answer in answers.items()},
        }
        return context

    def get_feedback_summary(self) -> Dict[str, Any]:
        """Build aggregated feedback summary statistics."""
        completed = [feedback for feedback in self._feedback_history if feedback.is_successful]
        needs_clarification = [
            feedback for feedback in self._feedback_history if feedback.status == FeedbackStatus.NEEDS_CLARIFICATION
        ]
        blocked = [feedback for feedback in self._feedback_history if feedback.is_blocked]
        failed = [feedback for feedback in self._feedback_history if feedback.status == FeedbackStatus.FAILED]
        summary = {
            "total": len(self._feedback_history),
            "completed": len(completed),
            "needs_clarification": len(needs_clarification),
            "blocked": len(blocked),
            "failed": len(failed),
            "completed_delegations": [feedback.delegation_id for feedback in completed],
            "pending_questions": self.get_pending_clarifications(needs_clarification + blocked),
            "blockers": [
                {"delegation": feedback.delegation_id, "blockers": feedback.blockers}
                for feedback in blocked
            ],
            "errors": [
                {"delegation": feedback.delegation_id, "error": feedback.error}
                for feedback in failed
            ],
        }
        return summary

    def get_feedback_for_delegation(
        self,
        delegation_id: str,
    ) -> List[AgentFeedback]:
        """Get feedback history entries for one delegation."""
        result = [
            feedback
            for feedback in self._feedback_history
            if feedback.delegation_id == delegation_id
        ]
        return result

    def get_latest_feedback_for_delegation(
        self,
        delegation_id: str,
    ) -> Optional[AgentFeedback]:
        """Get most recent feedback for one delegation."""
        feedbacks = self.get_feedback_for_delegation(delegation_id)
        result = feedbacks[-1] if feedbacks else None
        return result

    def has_unresolved_clarifications(self) -> bool:
        """Check whether history contains unresolved clarifications."""
        result = any(feedback.needs_clarification for feedback in self._feedback_history)
        return result

    def get_all_blockers(self) -> List[Dict[str, Any]]:
        """Get all blocking items from history."""
        blockers: List[Dict[str, Any]] = []
        for feedback in self._feedback_history:
            if feedback.blockers:
                blockers.append(
                    {
                        "agent": feedback.agent,
                        "delegation_id": feedback.delegation_id,
                        "blockers": feedback.blockers,
                    }
                )
        return blockers

    def clear_history(self) -> None:
        """Clear feedback history."""
        self._feedback_history.clear()

    @property
    def history(self) -> List[AgentFeedback]:
        """Get feedback history copy."""
        result = self._feedback_history.copy()
        return result
