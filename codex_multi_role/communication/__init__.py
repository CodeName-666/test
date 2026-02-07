"""Communication module for planner-gated multi-agent orchestration."""
from __future__ import annotations

from .contracts import (
    ALLOWED_WORKER_STATUSES,
    BLOCKED_STATUS,
    COMPLETED_STATUS,
    FAILED_STATUS,
    ContextPacket,
    DetailIndexEntry,
    WorkerOutput,
    WorkerOutputValidation,
    WorkerOutputValidator,
    build_question_id,
)
from .interaction import (
    Answer,
    CallbackUserInteraction,
    ConsoleUserInteraction,
    MockUserInteraction,
    Question,
    UserInteraction,
)
from .feedback import AgentFeedback, FeedbackLoop, FeedbackStatus
from .decision import PlannerDecision
from .coordinator import CommunicationCoordinator
from .engine import CommunicationEngine
from .ports import ExecutionResultLike, LoggerPort, RunStorePort

__all__ = [
    "ALLOWED_WORKER_STATUSES",
    "BLOCKED_STATUS",
    "COMPLETED_STATUS",
    "FAILED_STATUS",
    "ContextPacket",
    "DetailIndexEntry",
    "WorkerOutput",
    "WorkerOutputValidation",
    "WorkerOutputValidator",
    "build_question_id",
    "Answer",
    "CallbackUserInteraction",
    "ConsoleUserInteraction",
    "MockUserInteraction",
    "Question",
    "UserInteraction",
    "AgentFeedback",
    "FeedbackLoop",
    "FeedbackStatus",
    "PlannerDecision",
    "CommunicationCoordinator",
    "CommunicationEngine",
    "ExecutionResultLike",
    "LoggerPort",
    "RunStorePort",
]
