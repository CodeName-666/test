"""Dynamic package for dynamic orchestration architecture.

This package implements the dynamic orchestration pattern where the Planner
acts as the central decision-making hub, dynamically delegating to other agents,
handling user interaction, and processing feedback.
"""
from __future__ import annotations

from .dynamic_orchestrator import DynamicOrchestrator
from .agent_registry import AgentPolicy, AgentRegistry, redact_secrets
from .user_interaction import (
    Answer,
    CallbackUserInteraction,
    ConsoleUserInteraction,
    MockUserInteraction,
    Question,
    UserInteraction,
)
from .delegation_manager import (
    AgentType,
    Delegation,
    DelegationManager,
    DelegationStatus,
)
from .feedback_loop import AgentFeedback, FeedbackLoop, FeedbackStatus
from .parallel_executor import ExecutionResult, ParallelExecutor, WaveResult
from .role_client_factory import RoleClientFactory, ClientInstance
from .run_store import RunStore
from .runtime_models import (
    ContextPacket,
    DetailIndexEntry,
    WorkerOutput,
    WorkerOutputValidation,
    WorkerOutputValidator,
    build_question_id,
)

__all__ = [
    # Dynamic Orchestrator
    "DynamicOrchestrator",
    "AgentPolicy",
    "AgentRegistry",
    "redact_secrets",
    # Client Factory (multi-instance support)
    "RoleClientFactory",
    "ClientInstance",
    # User Interaction
    "Answer",
    "CallbackUserInteraction",
    "ConsoleUserInteraction",
    "MockUserInteraction",
    "Question",
    "UserInteraction",
    # Delegation
    "AgentType",
    "Delegation",
    "DelegationManager",
    "DelegationStatus",
    # Feedback
    "AgentFeedback",
    "FeedbackLoop",
    "FeedbackStatus",
    # Parallel Execution
    "ExecutionResult",
    "ParallelExecutor",
    "WaveResult",
    # Run persistence
    "RunStore",
    # Runtime models
    "ContextPacket",
    "DetailIndexEntry",
    "WorkerOutput",
    "WorkerOutputValidation",
    "WorkerOutputValidator",
    "build_question_id",
]
