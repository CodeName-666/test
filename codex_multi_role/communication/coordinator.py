"""Coordinator for planner-gated agent communication flows."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .contracts import ContextPacket, DetailIndexEntry
from .decision import PlannerDecision
from .feedback import AgentFeedback, FeedbackLoop
from .interaction import Answer, Question, UserInteraction
from .ports import ExecutionResultLike, LoggerPort, RunStorePort


class CommunicationCoordinator:
    """Coordinates communication state between planner, workers, and user."""

    def __init__(
        self,
        run_store: RunStorePort,
        runs_directory: Path,
        planner_compact_artifact_relative: str,
        feedback_loop: FeedbackLoop,
        user_interaction: UserInteraction,
        logger: LoggerPort,
        redact_payload: Callable[[Any], Any],
        record_manifest_event: Callable[[str, Dict[str, Any], Optional[str]], None],
        build_idempotency_key: Callable[[str, Dict[str, Any]], str],
        record_metric: Callable[[Dict[str, Any], Optional[str]], None],
    ) -> None:
        """Initialize communication coordinator.

        Args:
            run_store: Persisted run storage.
            runs_directory: Base directory of the current run.
            planner_compact_artifact_relative: Planner compact artifact path.
            feedback_loop: Feedback normalizer and history tracker.
            user_interaction: User interaction adapter.
            logger: Logger interface.
            redact_payload: Payload redaction function.
            record_manifest_event: Manifest persistence callback.
            build_idempotency_key: Idempotency key builder callback.
            record_metric: Metric persistence callback.
        """
        self._run_store = run_store
        self._runs_directory = runs_directory
        self._planner_compact_artifact_relative = planner_compact_artifact_relative
        self._feedback_loop = feedback_loop
        self._user_interaction = user_interaction
        self._logger = logger
        self._redact_payload = redact_payload
        self._record_manifest_event = record_manifest_event
        self._build_idempotency_key = build_idempotency_key
        self._record_metric = record_metric

    def build_initial_context(self, goal: str) -> Dict[str, Any]:
        """Build initial planner context.

        Args:
            goal: Run goal string.

        Returns:
            Initial context dictionary.
        """
        planner_compact = str(
            (self._run_store.artifacts_directory / self._planner_compact_artifact_relative)
            .relative_to(self._runs_directory)
        )
        context = {
            "goal": goal,
            "iteration": 0,
            "completed_delegations": [],
            "user_answers": {},
            "agent_results": {},
            "pending_questions": [],
            "answered_questions": self.load_answered_questions(),
            "active_assumptions": [],
            "planner_compact": planner_compact,
        }
        return context

    def extract_critical_questions(self, questions: List[Question]) -> List[Question]:
        """Return only critical planner questions.

        Args:
            questions: Planner provided questions.

        Returns:
            Filtered critical question list.
        """
        critical_questions = [question for question in questions if question.category == "critical"]
        return critical_questions

    def handle_user_questions(self, questions: List[Question]) -> Dict[str, Answer]:
        """Ask user critical questions and map answers by id.

        Args:
            questions: Critical questions from planner.

        Returns:
            Mapping from question id to answer.
        """
        answer_map: Dict[str, Answer]
        if questions:
            self._user_interaction.notify(
                f"The Planner needs {len(questions)} critical answer(s) to proceed."
            )
            answers = self._user_interaction.ask_questions(questions)
            answer_map = {answer.question_id: answer for answer in answers}
        else:
            answer_map = {}
        return answer_map

    def merge_user_answers(
        self,
        context: Dict[str, Any],
        answers: Dict[str, Answer],
    ) -> Dict[str, Any]:
        """Merge user answers into planner context and persistent storage.

        Args:
            context: Current planner context.
            answers: User answers by question id.

        Returns:
            Updated context dictionary.
        """
        context["user_answers"].update(
            {question_id: answer.answer for question_id, answer in answers.items()}
        )
        answered_records = context.get("answered_questions", [])
        if not isinstance(answered_records, list):
            answered_records = []
        for question_id, answer in answers.items():
            answer_record = {
                "question_id": question_id,
                "answer": answer.answer,
                "used_default": answer.used_default,
                "iteration": context.get("iteration", 0),
            }
            answered_records.append(answer_record)
            self._run_store.append_answer(
                self._redact_payload(answer_record),
                idempotency_key=f"answer:{question_id}",
            )
            self._record_manifest_event(
                "user_answer",
                answer_record,
                idempotency_key=f"user_answer:{question_id}",
            )
        context["answered_questions"] = answered_records
        pending_questions = context.get("pending_questions", [])
        if isinstance(pending_questions, list):
            unresolved = [
                question_payload
                for question_payload in pending_questions
                if question_payload.get("id") not in answers
            ]
            context["pending_questions"] = unresolved
        return context

    def process_execution_results(
        self,
        results: List[ExecutionResultLike],
        update_delegation_status: Callable[[AgentFeedback], None],
    ) -> Tuple[List[AgentFeedback], List[Question]]:
        """Normalize execution results and collect planner-facing questions.

        Args:
            results: Worker execution results.
            update_delegation_status: Callback to update delegation lifecycle.

        Returns:
            Tuple of (feedback list, pending question list).
        """
        feedbacks: List[AgentFeedback] = []
        for execution_result in results:
            payload = execution_result.result or {}
            if not execution_result.success and payload.get("error") is None:
                payload = dict(payload)
                payload["error"] = execution_result.error or "Unknown delegation error"
            agent_name = getattr(execution_result, "agent", "unknown")
            feedback = self._feedback_loop.process_agent_result(
                agent_name,
                execution_result.delegation_id,
                payload,
            )
            feedbacks.append(feedback)
            update_delegation_status(feedback)
        pending_questions = self._feedback_loop.get_pending_clarifications(feedbacks)
        return feedbacks, pending_questions

    def merge_pending_questions(
        self,
        context: Dict[str, Any],
        questions: List[Question],
    ) -> Dict[str, Any]:
        """Merge pending clarification questions into planner context.

        Args:
            context: Current planner context.
            questions: New planner-facing questions.

        Returns:
            Updated context dictionary.
        """
        pending_questions = context.get("pending_questions", [])
        if not isinstance(pending_questions, list):
            pending_questions = []
        existing_ids = {
            question_payload.get("id")
            for question_payload in pending_questions
            if isinstance(question_payload, dict)
        }
        for question in questions:
            if question.id not in existing_ids:
                pending_questions.append(
                    {
                        "id": question.id,
                        "question": question.question,
                        "category": question.category,
                        "context": question.context,
                        "priority": question.priority,
                        "expected_answer_format": question.expected_answer_format,
                    }
                )
                existing_ids.add(question.id)
        context["pending_questions"] = pending_questions
        return context

    def merge_delegation_results(
        self,
        context: Dict[str, Any],
        results: List[ExecutionResultLike],
    ) -> Dict[str, Any]:
        """Merge successful delegation results and refresh context facts.

        Args:
            context: Current planner context.
            results: Delegation execution results.

        Returns:
            Updated context dictionary.
        """
        completed_ids = context.get("completed_delegations", [])
        if not isinstance(completed_ids, list):
            completed_ids = []
        agent_results = context.get("agent_results", {})
        if not isinstance(agent_results, dict):
            agent_results = {}
        for result in results:
            if result.success:
                completed_ids.append(result.delegation_id)
                agent_results[result.delegation_id] = result.result
        context["completed_delegations"] = completed_ids
        context["agent_results"] = agent_results
        context["answered_questions"] = self.load_answered_questions()
        context["active_assumptions"] = self.get_active_assumptions(
            self._run_store.load_pool()
        )
        context["iteration"] = context.get("iteration", 0) + 1
        return context

    def build_context_packet_for_delegation(self) -> ContextPacket:
        """Build context packet for worker delegations.

        Returns:
            ContextPacket with compact planner truth, detail index, answers, assumptions.
        """
        pool_document = self._run_store.load_pool()
        facts_value = pool_document.get("facts", [])
        detail_index: List[DetailIndexEntry] = []
        if isinstance(facts_value, list):
            for fact in facts_value:
                if not isinstance(fact, dict):
                    continue
                if fact.get("superseded_by") is not None:
                    continue
                detail_id = fact.get("id")
                content = fact.get("content")
                if not isinstance(detail_id, str) or not detail_id.strip():
                    continue
                if not isinstance(content, str) or not content.strip():
                    continue
                title = fact.get("origin", "detail")
                if not isinstance(title, str) or not title.strip():
                    title = "detail"
                tags = fact.get("source_refs", [])
                if not isinstance(tags, list):
                    tags = []
                summary = content[:280]
                detail_index.append(
                    DetailIndexEntry(
                        id=detail_id,
                        title=title,
                        summary=summary,
                        tags=[str(tag) for tag in tags],
                    )
                )
                if len(detail_index) >= 32:
                    break
        answered_questions = self.load_answered_questions()
        active_assumptions = self.get_active_assumptions(pool_document)
        planner_compact_path = self._run_store.artifacts_directory / self._planner_compact_artifact_relative
        planner_compact_relative = str(planner_compact_path.relative_to(self._runs_directory))
        context_packet = ContextPacket(
            planner_compact=planner_compact_relative,
            detail_index=detail_index,
            answered_questions=answered_questions,
            active_assumptions=active_assumptions,
        )
        return context_packet

    def persist_worker_payload(
        self,
        delegation_id: str,
        agent_id: str,
        payload: Dict[str, Any],
    ) -> None:
        """Persist worker payload and side-effect log to inbox/manifest.

        Args:
            delegation_id: Delegation identifier.
            agent_id: Agent identifier.
            payload: Raw worker payload.
        """
        redacted_payload = self._redact_payload(payload)
        inbox_record = {
            "delegation_id": delegation_id,
            "agent_id": agent_id,
            "payload": redacted_payload,
        }
        self._run_store.append_inbox(
            inbox_record,
            idempotency_key=self._build_idempotency_key(
                "inbox",
                {"delegation_id": delegation_id, "payload": redacted_payload},
            ),
        )
        self._record_manifest_event(
            "worker_output",
            inbox_record,
            idempotency_key=self._build_idempotency_key(
                "worker_output",
                {"delegation_id": delegation_id, "payload": redacted_payload},
            ),
        )
        raw_side_effect_log = payload.get("side_effect_log")
        if isinstance(raw_side_effect_log, list):
            normalized_side_effect_log: List[Dict[str, Any]] = []
            for side_effect_entry in raw_side_effect_log:
                if isinstance(side_effect_entry, dict):
                    normalized_side_effect_log.append(side_effect_entry)
                else:
                    normalized_side_effect_log.append(
                        {"event": str(side_effect_entry)}
                    )
            if normalized_side_effect_log:
                self._record_manifest_event(
                    "worker_side_effect_log",
                    {
                        "delegation_id": delegation_id,
                        "agent_id": agent_id,
                        "side_effect_log": self._redact_payload(normalized_side_effect_log),
                    },
                    idempotency_key=self._build_idempotency_key(
                        "worker_side_effect_log",
                        {
                            "delegation_id": delegation_id,
                            "side_effect_log": normalized_side_effect_log,
                        },
                    ),
                )

    def persist_wave_outputs(
        self,
        context: Dict[str, Any],
        decision: PlannerDecision,
        feedbacks: List[AgentFeedback],
        results: List[ExecutionResultLike],
        wave_duration_s: float,
        current_wave_index: int,
    ) -> int:
        """Persist wave documents, pool merge, metrics and manifest events.

        Args:
            context: Planner context to update.
            decision: Planner decision for this wave.
            feedbacks: Worker feedback entries.
            results: Delegation execution results.
            wave_duration_s: Wave duration in seconds.
            current_wave_index: Current wave counter.

        Returns:
            Updated wave counter after persistence.
        """
        wave_index = current_wave_index + 1
        compact_path, detailed_path = self._write_wave_documents(
            wave_index=wave_index,
            decision=decision,
            feedbacks=feedbacks,
            results=results,
        )
        context["planner_compact"] = str(
            compact_path.relative_to(self._runs_directory)
        )
        planner_decision_payload = self._derive_planner_decision_payload(
            decision,
            results,
        )
        planner_decision_payload = self._merge_conflicts_into_planner_decision(
            planner_decision_payload,
            feedbacks,
        )
        pool_entries = self._build_pool_entries(feedbacks, wave_index)
        updated_pool = self._run_store.merge_pool_entries(pool_entries)
        self._record_wave_metrics(
            wave_index=wave_index,
            results=results,
            planner_decision_payload=planner_decision_payload,
            wave_duration_s=wave_duration_s,
        )
        self._record_manifest_event(
            "wave_completed",
            {
                "wave": wave_index,
                "compact_path": str(compact_path.relative_to(self._runs_directory)),
                "detailed_path": str(detailed_path.relative_to(self._runs_directory)),
                "planner_decision": planner_decision_payload,
                "pool_size": len(updated_pool.get("facts", [])),
            },
            idempotency_key=self._build_idempotency_key(
                "wave_completed",
                {"wave": wave_index},
            ),
        )
        return wave_index

    def load_answered_questions(self) -> List[Dict[str, Any]]:
        """Load normalized answered questions for context packets.

        Returns:
            List of normalized answers with `question_id` and `answer`.
        """
        answer_records = self._run_store.load_answers()
        normalized_answers: List[Dict[str, Any]] = []
        for answer_record in answer_records:
            if isinstance(answer_record, dict):
                question_id = answer_record.get("question_id")
                answer_value = answer_record.get("answer")
                if isinstance(question_id, str) and isinstance(answer_value, str):
                    normalized_answers.append(
                        {
                            "question_id": question_id,
                            "answer": answer_value,
                        }
                    )
        return normalized_answers

    def get_active_assumptions(self, pool_document: Dict[str, Any]) -> List[str]:
        """Extract active assumptions from pool document.

        Args:
            pool_document: Pool document payload.

        Returns:
            Active assumption contents.
        """
        assumptions: List[str] = []
        facts = pool_document.get("facts", [])
        if isinstance(facts, list):
            for fact in facts:
                if not isinstance(fact, dict):
                    continue
                is_assumption = bool(fact.get("is_assumption", False))
                not_superseded = fact.get("superseded_by") is None
                content_value = fact.get("content")
                if is_assumption and not_superseded and isinstance(content_value, str):
                    assumptions.append(content_value)
        return assumptions

    def _write_wave_documents(
        self,
        wave_index: int,
        decision: PlannerDecision,
        feedbacks: List[AgentFeedback],
        results: List[ExecutionResultLike],
    ) -> Tuple[Path, Path]:
        wave_compact_md = self._build_wave_compact(wave_index, decision, feedbacks, results)
        wave_detailed_md = self._build_wave_detailed(wave_index, decision, feedbacks, results)
        compact_path, detailed_path = self._run_store.write_wave_documents(
            wave_index,
            wave_compact_md,
            wave_detailed_md,
        )
        self._run_store.write_artifact(
            self._planner_compact_artifact_relative,
            wave_compact_md,
        )
        return compact_path, detailed_path

    def _merge_conflicts_into_planner_decision(
        self,
        planner_decision_payload: Dict[str, Any],
        feedbacks: List[AgentFeedback],
    ) -> Dict[str, Any]:
        merged_payload = dict(planner_decision_payload)
        detected_conflicts = self._detect_feedback_conflicts(feedbacks)
        existing_conflicts = merged_payload.get("conflicts_resolved", [])
        if isinstance(existing_conflicts, list):
            merged_conflicts = list(existing_conflicts)
        else:
            merged_conflicts = []
        for conflict in detected_conflicts:
            if conflict not in merged_conflicts:
                merged_conflicts.append(conflict)
        merged_payload["conflicts_resolved"] = merged_conflicts
        if detected_conflicts:
            io_status_value = merged_payload.get("io_status")
            if not isinstance(io_status_value, str) or io_status_value.upper() == "OK":
                merged_payload["io_status"] = "NOT_OK"
            reasons_value = merged_payload.get("not_ok_reasons", [])
            if isinstance(reasons_value, list):
                not_ok_reasons = list(reasons_value)
            else:
                not_ok_reasons = []
            for conflict in detected_conflicts:
                conflict_reason = f"conflict_detected: {conflict}"
                if conflict_reason not in not_ok_reasons:
                    not_ok_reasons.append(conflict_reason)
            merged_payload["not_ok_reasons"] = not_ok_reasons
        return merged_payload

    def _record_wave_metrics(
        self,
        wave_index: int,
        results: List[ExecutionResultLike],
        planner_decision_payload: Dict[str, Any],
        wave_duration_s: float,
    ) -> None:
        successful_results = [result for result in results if result.success]
        success_rate = 0.0
        if results:
            success_rate = len(successful_results) / len(results)
        self._record_metric(
            {
                "metric": "wave_time",
                "wave": wave_index,
                "value_s": wave_duration_s,
            },
            idempotency_key=self._build_idempotency_key(
                "wave_time",
                {"wave": wave_index, "value_s": wave_duration_s},
            ),
        )
        self._record_metric(
            {
                "metric": "wave_duration",
                "wave": wave_index,
                "value_s": wave_duration_s,
            },
            idempotency_key=self._build_idempotency_key(
                "wave_duration",
                {"wave": wave_index, "value_s": wave_duration_s},
            ),
        )
        self._record_metric(
            {
                "metric": "agent_success_rate",
                "wave": wave_index,
                "value": success_rate,
            },
            idempotency_key=self._build_idempotency_key(
                "agent_success_rate",
                {"wave": wave_index, "value": success_rate},
            ),
        )
        if planner_decision_payload.get("io_status") == "NOT_OK":
            self._record_metric(
                {
                    "metric": "not_ok_reasons",
                    "wave": wave_index,
                    "reasons": planner_decision_payload.get("not_ok_reasons", []),
                },
                idempotency_key=self._build_idempotency_key(
                    "not_ok_reasons",
                    {
                        "wave": wave_index,
                        "reasons": planner_decision_payload.get("not_ok_reasons", []),
                    },
                ),
            )
            self._record_metric(
                {
                    "metric": "failure_reasons",
                    "wave": wave_index,
                    "reasons": planner_decision_payload.get("not_ok_reasons", []),
                },
                idempotency_key=self._build_idempotency_key(
                    "failure_reasons",
                    {
                        "wave": wave_index,
                        "reasons": planner_decision_payload.get("not_ok_reasons", []),
                    },
                ),
            )

    def _build_wave_compact(
        self,
        wave_index: int,
        decision: PlannerDecision,
        feedbacks: List[AgentFeedback],
        results: List[ExecutionResultLike],
    ) -> str:
        compact_text: str
        if decision.wave_compact_md.strip():
            compact_text = decision.wave_compact_md
        else:
            summary_lines = [
                f"# Wave {wave_index} Compact",
                "",
                f"Planner summary: {decision.summary or 'No planner summary provided.'}",
                "",
                "## Delegation Results",
            ]
            for result in results:
                agent_name = getattr(result, "agent", "unknown")
                status = "ok" if result.success else "failed"
                summary_lines.append(f"- `{result.delegation_id}` ({agent_name}): {status}")
            blocked_feedbacks = [feedback for feedback in feedbacks if feedback.is_blocked]
            if blocked_feedbacks:
                summary_lines.append("")
                summary_lines.append("## Blocked")
                for feedback in blocked_feedbacks:
                    summary_lines.append(
                        f"- `{feedback.delegation_id}`: {'; '.join(feedback.blockers) or 'blocked'}"
                    )
            compact_text = "\n".join(summary_lines) + "\n"
        return compact_text

    def _build_wave_detailed(
        self,
        wave_index: int,
        decision: PlannerDecision,
        feedbacks: List[AgentFeedback],
        results: List[ExecutionResultLike],
    ) -> str:
        detailed_text: str
        if decision.wave_detailed_md.strip():
            detailed_text = decision.wave_detailed_md
        else:
            lines = [
                f"# Wave {wave_index} Detailed",
                "",
                "## Planner Summary",
                decision.summary or "No planner summary provided.",
                "",
                "## Delegation Outcomes",
            ]
            for result in results:
                agent_name = getattr(result, "agent", "unknown")
                lines.append(f"### {result.delegation_id} ({agent_name})")
                lines.append(f"- success: {result.success}")
                lines.append(f"- duration_s: {result.duration_s}")
                if result.error:
                    lines.append(f"- error: {result.error}")
                payload = result.result or {}
                lines.append("- payload:")
                lines.append("```json")
                lines.append(json.dumps(self._redact_payload(payload), ensure_ascii=False, indent=2))
                lines.append("```")
            if feedbacks:
                lines.append("")
                lines.append("## Feedback Summary")
                feedback_summary = self._feedback_loop.get_feedback_summary()
                lines.append("```json")
                lines.append(
                    json.dumps(self._redact_payload(feedback_summary), ensure_ascii=False, indent=2)
                )
                lines.append("```")
            detailed_text = "\n".join(lines) + "\n"
        return detailed_text

    def _detect_feedback_conflicts(self, feedbacks: List[AgentFeedback]) -> List[str]:
        criteria_resolution: Dict[str, Dict[str, List[str]]] = {}
        for feedback in feedbacks:
            worker_output = feedback.worker_output
            if worker_output is None:
                continue
            met = worker_output.coverage.get("criteria_met", [])
            unmet = worker_output.coverage.get("criteria_unmet", [])
            for criterion in met:
                criterion_bucket = criteria_resolution.setdefault(
                    criterion,
                    {"met": [], "unmet": []},
                )
                criterion_bucket["met"].append(feedback.delegation_id)
            for criterion in unmet:
                criterion_bucket = criteria_resolution.setdefault(
                    criterion,
                    {"met": [], "unmet": []},
                )
                criterion_bucket["unmet"].append(feedback.delegation_id)
        conflicts: List[str] = []
        for criterion, resolution in criteria_resolution.items():
            if resolution["met"] and resolution["unmet"]:
                conflicts.append(
                    (
                        f"criterion '{criterion}' met by {resolution['met']} "
                        f"but unmet by {resolution['unmet']}"
                    )
                )
        return conflicts

    def _derive_planner_decision_payload(
        self,
        decision: PlannerDecision,
        results: List[ExecutionResultLike],
    ) -> Dict[str, Any]:
        planner_decision_payload = dict(decision.planner_decision)
        if "io_status" not in planner_decision_payload:
            failed_results = [result for result in results if not result.success]
            if failed_results:
                io_status = "NOT_OK"
                not_ok_reasons = [
                    f"{result.delegation_id}: {result.error or 'unknown failure'}"
                    for result in failed_results
                ]
            else:
                io_status = "OK"
                not_ok_reasons = []
            planner_decision_payload = {
                "io_status": io_status,
                "not_ok_reasons": not_ok_reasons,
                "conflicts_resolved": [],
                "next_actions": [],
            }
        if "not_ok_reasons" not in planner_decision_payload:
            planner_decision_payload["not_ok_reasons"] = []
        if "conflicts_resolved" not in planner_decision_payload:
            planner_decision_payload["conflicts_resolved"] = []
        if "next_actions" not in planner_decision_payload:
            planner_decision_payload["next_actions"] = []
        return planner_decision_payload

    def _build_pool_entries(
        self,
        feedbacks: List[AgentFeedback],
        wave_index: int,
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        wave_ref = f"wave_{wave_index:02d}"
        for feedback in feedbacks:
            worker_output = feedback.worker_output
            if worker_output is None:
                continue
            compact_content = worker_output.compact_md.strip()
            if compact_content:
                confidence = 0.8
                if feedback.status.value == "failed":
                    confidence = 0.3
                elif feedback.status.value == "blocked":
                    confidence = 0.5
                entries.append(
                    {
                        "id": f"fact_{wave_index:02d}_{feedback.delegation_id}",
                        "content": compact_content,
                        "origin": "delegation",
                        "confidence": confidence,
                        "is_assumption": False,
                        "source_refs": [wave_ref, feedback.delegation_id],
                        "superseded_by": None,
                    }
                )
            for assumption_index, assumption in enumerate(worker_output.assumptions_made):
                entries.append(
                    {
                        "id": (
                            f"fact_{wave_index:02d}_{feedback.delegation_id}"
                            f"_assumption_{assumption_index}"
                        ),
                        "content": assumption,
                        "origin": "delegation",
                        "confidence": 0.4,
                        "is_assumption": True,
                        "source_refs": [wave_ref, feedback.delegation_id],
                        "superseded_by": None,
                    }
                )
        return entries


__all__ = ["CommunicationCoordinator", "ExecutionResultLike", "LoggerPort", "RunStorePort"]
