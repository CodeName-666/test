"""Feedback loop management for the Planner-as-Orchestrator architecture.

This module handles the flow of feedback from agents back to the Planner,
including processing agent results, routing clarification requests,
and maintaining feedback history for context building.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .user_interaction import Answer, Question, UserInteraction


class FeedbackStatus(Enum):
    """Status of agent feedback."""

    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class AgentFeedback:
    """Feedback from an agent back to the Planner.

    Attributes:
        agent: Name of the agent that produced this feedback.
        delegation_id: ID of the delegation this feedback relates to.
        status: Status of the agent's work.
        result: The agent's output payload.
        clarification_questions: Questions the agent needs answered.
        blockers: List of issues blocking the agent.
        error: Error message if the agent failed.
    """

    agent: str
    delegation_id: str
    status: FeedbackStatus
    result: Dict[str, Any] = field(default_factory=dict)
    clarification_questions: List[Question] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate feedback fields after initialization."""
        if not self.agent or not isinstance(self.agent, str):
            raise ValueError("AgentFeedback agent must be a non-empty string")
        if not self.delegation_id or not isinstance(self.delegation_id, str):
            raise ValueError("AgentFeedback delegation_id must be a non-empty string")

        # Convert status string to enum if needed
        if isinstance(self.status, str):
            object.__setattr__(self, "status", FeedbackStatus(self.status))

    @property
    def needs_clarification(self) -> bool:
        """Check if this feedback requires clarification."""
        return self.status == FeedbackStatus.NEEDS_CLARIFICATION

    @property
    def is_blocked(self) -> bool:
        """Check if the agent is blocked."""
        return self.status == FeedbackStatus.BLOCKED

    @property
    def is_successful(self) -> bool:
        """Check if the agent completed successfully."""
        return self.status == FeedbackStatus.COMPLETED


class FeedbackLoop:
    """Manages feedback flow from agents back to Planner.

    Key responsibilities:
    - Aggregate feedback from multiple agents
    - Route clarification requests through Planner to user
    - Track feedback history for context building
    - Convert raw agent results to structured feedback
    """

    def __init__(
        self,
        user_interaction: UserInteraction,
    ) -> None:
        """Initialize the feedback loop.

        Args:
            user_interaction: Interface for user communication.
        """
        self._user_interaction = user_interaction
        self._feedback_history: List[AgentFeedback] = []

    def process_agent_result(
        self,
        agent: str,
        delegation_id: str,
        result: Dict[str, Any],
    ) -> AgentFeedback:
        """Convert raw agent result to structured feedback.

        Args:
            agent: Name of the agent that produced the result.
            delegation_id: ID of the delegation.
            result: Raw result payload from the agent.

        Returns:
            Structured AgentFeedback object.
        """
        # Determine status from result
        needs_clarification = result.get("needs_clarification", False)
        has_error = result.get("error") is not None
        has_blockers = bool(result.get("blockers", []))

        if has_error:
            status = FeedbackStatus.FAILED
        elif needs_clarification:
            status = FeedbackStatus.NEEDS_CLARIFICATION
        elif has_blockers:
            status = FeedbackStatus.BLOCKED
        else:
            status = FeedbackStatus.COMPLETED

        # Extract clarification questions
        questions: List[Question] = []
        if needs_clarification:
            raw_questions = result.get("questions", [])
            for i, q in enumerate(raw_questions):
                question_id = f"{delegation_id}_{q.get('id', str(i))}"
                questions.append(
                    Question(
                        id=question_id,
                        question=q.get("question", ""),
                        category=q.get("category", "optional"),
                        default_suggestion=q.get("default_suggestion"),
                        context=q.get("context"),
                    )
                )

        # Extract blockers
        blockers = result.get("blockers", [])

        feedback = AgentFeedback(
            agent=agent,
            delegation_id=delegation_id,
            status=status,
            result=result,
            clarification_questions=questions,
            blockers=blockers,
            error=result.get("error"),
        )

        self._feedback_history.append(feedback)
        return feedback

    def get_pending_clarifications(
        self,
        feedbacks: List[AgentFeedback],
    ) -> List[Question]:
        """Extract all pending clarification questions from feedbacks.

        Args:
            feedbacks: List of agent feedbacks to process.

        Returns:
            List of all clarification questions.
        """
        questions: List[Question] = []
        for feedback in feedbacks:
            if feedback.needs_clarification:
                questions.extend(feedback.clarification_questions)
        return questions

    def route_clarifications_to_user(
        self,
        feedbacks: List[AgentFeedback],
    ) -> Dict[str, Answer]:
        """Route clarification requests directly to user.

        Args:
            feedbacks: List of feedbacks with clarification requests.

        Returns:
            Dict mapping question IDs to user answers.
        """
        questions = self.get_pending_clarifications(feedbacks)

        if not questions:
            return {}

        # Notify user about clarification request
        agent_names = {f.agent for f in feedbacks if f.needs_clarification}
        self._user_interaction.notify(
            f"Agent(s) {', '.join(agent_names)} need clarification."
        )

        # Ask user for answers
        answers = self._user_interaction.ask_questions(questions)

        # Build answer map
        answer_map: Dict[str, Answer] = {a.question_id: a for a in answers}

        return answer_map

    def build_clarification_context(
        self,
        answers: Dict[str, Answer],
        delegation_id: str,
    ) -> Dict[str, Any]:
        """Build context payload with clarification answers for re-running agent.

        Args:
            answers: Dict of question ID to Answer.
            delegation_id: ID of the delegation to build context for.

        Returns:
            Context dict with clarification answers.
        """
        # Filter answers relevant to this delegation
        relevant_answers = {
            q_id: ans
            for q_id, ans in answers.items()
            if q_id.startswith(f"{delegation_id}_")
        }

        # Build context
        context: Dict[str, Any] = {
            "clarifications": {
                q_id.replace(f"{delegation_id}_", ""): ans.answer
                for q_id, ans in relevant_answers.items()
            }
        }

        return context

    def get_feedback_summary(self) -> Dict[str, Any]:
        """Get a summary of all feedback in history.

        Returns:
            Dict with feedback statistics and details.
        """
        completed = [f for f in self._feedback_history if f.is_successful]
        needs_clarification = [f for f in self._feedback_history if f.needs_clarification]
        blocked = [f for f in self._feedback_history if f.is_blocked]
        failed = [f for f in self._feedback_history if f.status == FeedbackStatus.FAILED]

        return {
            "total": len(self._feedback_history),
            "completed": len(completed),
            "needs_clarification": len(needs_clarification),
            "blocked": len(blocked),
            "failed": len(failed),
            "completed_delegations": [f.delegation_id for f in completed],
            "pending_questions": self.get_pending_clarifications(needs_clarification),
            "blockers": [
                {"delegation": f.delegation_id, "blockers": f.blockers}
                for f in blocked
            ],
            "errors": [
                {"delegation": f.delegation_id, "error": f.error}
                for f in failed
            ],
        }

    def get_feedback_for_delegation(
        self,
        delegation_id: str,
    ) -> List[AgentFeedback]:
        """Get all feedback entries for a specific delegation.

        Args:
            delegation_id: ID of the delegation.

        Returns:
            List of feedback entries for this delegation.
        """
        return [
            f for f in self._feedback_history if f.delegation_id == delegation_id
        ]

    def get_latest_feedback_for_delegation(
        self,
        delegation_id: str,
    ) -> Optional[AgentFeedback]:
        """Get the most recent feedback for a delegation.

        Args:
            delegation_id: ID of the delegation.

        Returns:
            Most recent feedback, or None if not found.
        """
        feedbacks = self.get_feedback_for_delegation(delegation_id)
        return feedbacks[-1] if feedbacks else None

    def has_unresolved_clarifications(self) -> bool:
        """Check if there are any unresolved clarification requests.

        Returns:
            True if any feedback needs clarification.
        """
        return any(f.needs_clarification for f in self._feedback_history)

    def get_all_blockers(self) -> List[Dict[str, Any]]:
        """Get all blockers from all feedbacks.

        Returns:
            List of blocker information.
        """
        blockers: List[Dict[str, Any]] = []
        for feedback in self._feedback_history:
            if feedback.blockers:
                blockers.append({
                    "agent": feedback.agent,
                    "delegation_id": feedback.delegation_id,
                    "blockers": feedback.blockers,
                })
        return blockers

    def clear_history(self) -> None:
        """Clear the feedback history."""
        self._feedback_history.clear()

    @property
    def history(self) -> List[AgentFeedback]:
        """Get a copy of the feedback history."""
        return self._feedback_history.copy()
