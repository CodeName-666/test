"""User interaction abstraction for the Planner-as-Orchestrator architecture.

This module provides an abstraction layer for user interaction, allowing
different implementations (console, callback, API) to be used interchangeably.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Question:
    """Represents a question to ask the user.

    Attributes:
        id: Unique identifier for the question.
        question: The question text to display.
        category: Priority category ("critical" or "optional").
        default_suggestion: Optional default value if user provides no input.
        context: Optional context explaining why this question matters.
    """

    id: str
    question: str
    category: str = "optional"
    default_suggestion: Optional[str] = None
    context: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate question fields after initialization."""
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Question id must be a non-empty string")
        if not self.question or not isinstance(self.question, str):
            raise ValueError("Question text must be a non-empty string")
        if self.category not in ("critical", "optional"):
            raise ValueError("Question category must be 'critical' or 'optional'")


@dataclass
class Answer:
    """User's answer to a question.

    Attributes:
        question_id: ID of the question being answered.
        answer: The user's response text.
        used_default: Whether the default suggestion was used.
    """

    question_id: str
    answer: str
    used_default: bool = False

    def __post_init__(self) -> None:
        """Validate answer fields after initialization."""
        if not self.question_id or not isinstance(self.question_id, str):
            raise ValueError("Answer question_id must be a non-empty string")
        if not isinstance(self.answer, str):
            raise ValueError("Answer must be a string")


class UserInteraction(ABC):
    """Abstract interface for user interaction.

    This abstraction allows different implementations:
    - ConsoleUserInteraction: Direct stdin/stdout
    - CallbackUserInteraction: External callback function
    - MockUserInteraction: For testing

    All implementations must handle the core interaction patterns:
    asking questions, sending notifications, and requesting confirmations.
    """

    @abstractmethod
    def ask_questions(
        self,
        questions: List[Question],
        timeout_s: Optional[float] = None,
    ) -> List[Answer]:
        """Present questions to the user and collect answers.

        Args:
            questions: List of Question objects to ask.
            timeout_s: Optional timeout in seconds for user response.

        Returns:
            List of Answer objects with user responses.

        Raises:
            TimeoutError: If timeout_s is specified and exceeded.
        """
        ...

    @abstractmethod
    def notify(self, message: str) -> None:
        """Send a notification message to the user.

        Args:
            message: The message to display.
        """
        ...

    @abstractmethod
    def request_confirmation(
        self,
        message: str,
        default: bool = False,
    ) -> bool:
        """Request yes/no confirmation from the user.

        Args:
            message: The confirmation prompt to display.
            default: Default value if user provides no input.

        Returns:
            True if user confirms, False otherwise.
        """
        ...


class ConsoleUserInteraction(UserInteraction):
    """Console-based user interaction via stdin/stdout.

    This implementation provides interactive prompts for CLI usage.
    Questions are displayed with their category and optional defaults,
    and user input is collected via standard input.
    """

    def __init__(
        self,
        auto_use_defaults: bool = False,
        prompt_prefix: str = "> ",
    ) -> None:
        """Initialize console interaction.

        Args:
            auto_use_defaults: If True, automatically use default values.
            prompt_prefix: Prefix string for input prompts.
        """
        self._auto_use_defaults = auto_use_defaults
        self._prompt_prefix = prompt_prefix

    def ask_questions(
        self,
        questions: List[Question],
        timeout_s: Optional[float] = None,
    ) -> List[Answer]:
        """Present questions to the user via console and collect answers.

        Args:
            questions: List of Question objects to ask.
            timeout_s: Optional timeout (not implemented for console).

        Returns:
            List of Answer objects with user responses.
        """
        if not questions:
            return []

        answers: List[Answer] = []

        for question in questions:
            # Build the prompt display
            category_label = question.category.upper()
            prompt_lines = [
                f"\n[{category_label}] {question.question}",
            ]

            if question.context:
                prompt_lines.append(f"  Context: {question.context}")

            if question.default_suggestion:
                prompt_lines.append(f"  (Default: {question.default_suggestion})")

            prompt_lines.append(self._prompt_prefix)

            # Display prompt
            full_prompt = "\n".join(prompt_lines[:-1]) + "\n" + prompt_lines[-1]
            print(full_prompt, end="", flush=True)

            # Get user input or use default
            if self._auto_use_defaults and question.default_suggestion:
                user_input = ""
                print(f"[Auto-using default: {question.default_suggestion}]")
            else:
                user_input = input().strip()

            # Determine final answer
            if not user_input and question.default_suggestion:
                answers.append(
                    Answer(
                        question_id=question.id,
                        answer=question.default_suggestion,
                        used_default=True,
                    )
                )
            else:
                answers.append(
                    Answer(
                        question_id=question.id,
                        answer=user_input,
                        used_default=False,
                    )
                )

        return answers

    def notify(self, message: str) -> None:
        """Send a notification message via console output.

        Args:
            message: The message to display.
        """
        print(f"\n[INFO] {message}")

    def request_confirmation(
        self,
        message: str,
        default: bool = False,
    ) -> bool:
        """Request yes/no confirmation via console.

        Args:
            message: The confirmation prompt to display.
            default: Default value if user provides no input.

        Returns:
            True if user confirms, False otherwise.
        """
        default_hint = "[Y/n]" if default else "[y/N]"
        print(f"\n{message} {default_hint}", end=" ", flush=True)

        if self._auto_use_defaults:
            print(f"[Auto-using default: {'Yes' if default else 'No'}]")
            return default

        user_input = input().strip().lower()

        if not user_input:
            return default

        return user_input in ("y", "yes", "ja", "j")


class CallbackUserInteraction(UserInteraction):
    """Callback-based user interaction for external integration.

    This implementation allows external systems (GUI, API, etc.) to
    provide callback functions for handling user interaction.
    """

    def __init__(
        self,
        question_callback: Callable[[List[Question]], List[Answer]],
        notify_callback: Optional[Callable[[str], None]] = None,
        confirmation_callback: Optional[Callable[[str, bool], bool]] = None,
    ) -> None:
        """Initialize callback-based interaction.

        Args:
            question_callback: Function to handle question asking.
            notify_callback: Optional function to handle notifications.
            confirmation_callback: Optional function to handle confirmations.
        """
        if not callable(question_callback):
            raise TypeError("question_callback must be callable")

        self._question_callback = question_callback
        self._notify_callback = notify_callback
        self._confirmation_callback = confirmation_callback

    def ask_questions(
        self,
        questions: List[Question],
        timeout_s: Optional[float] = None,
    ) -> List[Answer]:
        """Present questions via callback and collect answers.

        Args:
            questions: List of Question objects to ask.
            timeout_s: Optional timeout (passed to callback if supported).

        Returns:
            List of Answer objects with user responses.
        """
        return self._question_callback(questions)

    def notify(self, message: str) -> None:
        """Send a notification via callback.

        Args:
            message: The message to send.
        """
        if self._notify_callback:
            self._notify_callback(message)

    def request_confirmation(
        self,
        message: str,
        default: bool = False,
    ) -> bool:
        """Request confirmation via callback.

        Args:
            message: The confirmation prompt.
            default: Default value if callback not provided.

        Returns:
            True if confirmed, False otherwise.
        """
        if self._confirmation_callback:
            return self._confirmation_callback(message, default)
        return default


class MockUserInteraction(UserInteraction):
    """Mock user interaction for testing purposes.

    This implementation allows predefined answers to be provided,
    making it useful for automated testing and simulations.
    """

    def __init__(
        self,
        predefined_answers: Optional[Dict[str, str]] = None,
        default_answer: str = "",
        default_confirmation: bool = True,
    ) -> None:
        """Initialize mock interaction with predefined responses.

        Args:
            predefined_answers: Dict mapping question IDs to answers.
            default_answer: Default answer for questions not in predefined_answers.
            default_confirmation: Default response for confirmation requests.
        """
        self._predefined_answers = predefined_answers or {}
        self._default_answer = default_answer
        self._default_confirmation = default_confirmation
        self._notifications: List[str] = []
        self._asked_questions: List[Question] = []

    def ask_questions(
        self,
        questions: List[Question],
        timeout_s: Optional[float] = None,
    ) -> List[Answer]:
        """Return predefined answers for questions.

        Args:
            questions: List of Question objects.
            timeout_s: Ignored in mock implementation.

        Returns:
            List of Answer objects with predefined or default responses.
        """
        self._asked_questions.extend(questions)
        answers: List[Answer] = []

        for question in questions:
            if question.id in self._predefined_answers:
                answers.append(
                    Answer(
                        question_id=question.id,
                        answer=self._predefined_answers[question.id],
                        used_default=False,
                    )
                )
            elif question.default_suggestion:
                answers.append(
                    Answer(
                        question_id=question.id,
                        answer=question.default_suggestion,
                        used_default=True,
                    )
                )
            else:
                answers.append(
                    Answer(
                        question_id=question.id,
                        answer=self._default_answer,
                        used_default=False,
                    )
                )

        return answers

    def notify(self, message: str) -> None:
        """Record notification for later inspection.

        Args:
            message: The message to record.
        """
        self._notifications.append(message)

    def request_confirmation(
        self,
        message: str,
        default: bool = False,
    ) -> bool:
        """Return predefined confirmation response.

        Args:
            message: Ignored in mock implementation.
            default: Ignored in mock implementation.

        Returns:
            The predefined default_confirmation value.
        """
        return self._default_confirmation

    @property
    def notifications(self) -> List[str]:
        """Get list of recorded notifications."""
        return self._notifications.copy()

    @property
    def asked_questions(self) -> List[Question]:
        """Get list of questions that were asked."""
        return self._asked_questions.copy()
