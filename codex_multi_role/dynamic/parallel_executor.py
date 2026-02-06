"""Parallel execution for the Planner-as-Orchestrator architecture.

This module provides parallel execution capabilities for agent delegations
using ThreadPoolExecutor. It handles execution of independent tasks
concurrently while respecting dependency ordering through waves.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .delegation_manager import Delegation, DelegationStatus


@dataclass
class ExecutionResult:
    """Result of executing a single delegation.

    Attributes:
        delegation_id: ID of the executed delegation.
        success: Whether execution completed without exception.
        result: The result payload if successful.
        error: Error message if execution failed.
        duration_s: Execution duration in seconds.
    """

    delegation_id: str
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_s: float = 0.0


@dataclass
class WaveResult:
    """Result of executing a wave of delegations.

    Attributes:
        wave_index: Index of the wave in the execution sequence.
        results: Dict mapping delegation ID to ExecutionResult.
        all_successful: Whether all delegations in the wave succeeded.
    """

    wave_index: int
    results: Dict[str, ExecutionResult] = field(default_factory=dict)

    @property
    def all_successful(self) -> bool:
        """Check if all delegations in this wave completed successfully."""
        return all(r.success for r in self.results.values())

    @property
    def failed_delegations(self) -> List[str]:
        """Get IDs of delegations that failed in this wave."""
        return [
            d_id for d_id, r in self.results.items() if not r.success
        ]

    @property
    def successful_delegations(self) -> List[str]:
        """Get IDs of delegations that succeeded in this wave."""
        return [
            d_id for d_id, r in self.results.items() if r.success
        ]


class ParallelExecutor:
    """Execute multiple agent delegations in parallel.

    Uses ThreadPoolExecutor since agents run as separate processes
    and we're primarily waiting on I/O. Provides wave-based execution
    to respect dependencies while maximizing parallelism.
    """

    def __init__(
        self,
        max_workers: int = 4,
        default_timeout_s: float = 600.0,
    ) -> None:
        """Initialize the parallel executor.

        Args:
            max_workers: Maximum number of concurrent workers.
            default_timeout_s: Default timeout for execution in seconds.
        """
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if default_timeout_s <= 0:
            raise ValueError("default_timeout_s must be positive")

        self._max_workers = max_workers
        self._default_timeout_s = default_timeout_s
        self._lock = threading.Lock()
        self._active_futures: Dict[str, Future] = {}

    def execute_single(
        self,
        delegation: Delegation,
        execute_fn: Callable[[Delegation], Dict[str, Any]],
        timeout_s: Optional[float] = None,
    ) -> ExecutionResult:
        """Execute a single delegation synchronously.

        Args:
            delegation: The delegation to execute.
            execute_fn: Function to execute the delegation.
            timeout_s: Optional timeout override.

        Returns:
            ExecutionResult with the outcome.
        """
        import time

        start_time = time.time()
        timeout = timeout_s or self._default_timeout_s
        result: ExecutionResult

        try:
            delegation.mark_running()
            payload = execute_fn(delegation)
            duration = time.time() - start_time

            error_value = payload.get("error")
            if error_value is None:
                delegation.mark_completed(payload)
                result = ExecutionResult(
                    delegation_id=delegation.id,
                    success=True,
                    result=payload,
                    duration_s=duration,
                )
            else:
                error_text = str(error_value)
                delegation.mark_failed(error_text)
                result = ExecutionResult(
                    delegation_id=delegation.id,
                    success=False,
                    result=payload,
                    error=error_text,
                    duration_s=duration,
                )

        except TimeoutError as exc:
            duration = time.time() - start_time
            error_text = f"Execution timed out after {timeout}s: {exc}"
            delegation.mark_failed(error_text)
            result = ExecutionResult(
                delegation_id=delegation.id,
                success=False,
                error=error_text,
                duration_s=duration,
            )

        except Exception as exc:
            duration = time.time() - start_time
            error_text = str(exc)
            delegation.mark_failed(error_text)
            result = ExecutionResult(
                delegation_id=delegation.id,
                success=False,
                error=error_text,
                duration_s=duration,
            )
        return result

    def execute_parallel(
        self,
        delegations: List[Delegation],
        execute_fn: Callable[[Delegation], Dict[str, Any]],
        timeout_s: Optional[float] = None,
    ) -> Dict[str, ExecutionResult]:
        """Execute delegations in parallel, returning results by ID.

        Args:
            delegations: List of delegations to execute.
            execute_fn: Function to execute a single delegation.
            timeout_s: Optional timeout override.

        Returns:
            Dict mapping delegation ID to ExecutionResult.
        """
        results: Dict[str, ExecutionResult] = {}
        if delegations:
            timeout = timeout_s or self._default_timeout_s
            with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                # Submit all delegations
                future_to_delegation: Dict[Future, Delegation] = {}

                for delegation in delegations:
                    future = executor.submit(
                        self._execute_with_tracking,
                        delegation,
                        execute_fn,
                    )
                    future_to_delegation[future] = delegation

                    with self._lock:
                        self._active_futures[delegation.id] = future

                # Collect results as they complete
                try:
                    for future in as_completed(
                        future_to_delegation.keys(),
                        timeout=timeout,
                    ):
                        delegation = future_to_delegation[future]

                        try:
                            result = future.result()
                            results[delegation.id] = result

                        except Exception as exc:
                            results[delegation.id] = ExecutionResult(
                                delegation_id=delegation.id,
                                success=False,
                                error=str(exc),
                            )

                        finally:
                            with self._lock:
                                self._active_futures.pop(delegation.id, None)

                except TimeoutError:
                    # Handle overall timeout
                    for future, delegation in future_to_delegation.items():
                        if delegation.id not in results:
                            future.cancel()
                            results[delegation.id] = ExecutionResult(
                                delegation_id=delegation.id,
                                success=False,
                                error=f"Overall timeout of {timeout}s exceeded",
                            )

        return results

    def _execute_with_tracking(
        self,
        delegation: Delegation,
        execute_fn: Callable[[Delegation], Dict[str, Any]],
    ) -> ExecutionResult:
        """Execute delegation with timing and error tracking.

        Args:
            delegation: The delegation to execute.
            execute_fn: Function to execute the delegation.

        Returns:
            ExecutionResult with the outcome.
        """
        import time

        start_time = time.time()
        result: ExecutionResult

        try:
            delegation.mark_running()
            payload = execute_fn(delegation)
            duration = time.time() - start_time

            # Check if result indicates clarification needed
            error_value = payload.get("error")
            if error_value is None:
                if payload.get("needs_clarification"):
                    delegation.mark_needs_clarification()
                else:
                    delegation.mark_completed(payload)
                result = ExecutionResult(
                    delegation_id=delegation.id,
                    success=True,
                    result=payload,
                    duration_s=duration,
                )
            else:
                error_text = str(error_value)
                delegation.mark_failed(error_text)
                result = ExecutionResult(
                    delegation_id=delegation.id,
                    success=False,
                    result=payload,
                    error=error_text,
                    duration_s=duration,
                )

        except Exception as exc:
            duration = time.time() - start_time
            error_text = str(exc)
            delegation.mark_failed(error_text)
            result = ExecutionResult(
                delegation_id=delegation.id,
                success=False,
                error=error_text,
                duration_s=duration,
            )
        return result

    def execute_waves(
        self,
        waves: List[List[Delegation]],
        execute_fn: Callable[[Delegation], Dict[str, Any]],
        timeout_s: Optional[float] = None,
        stop_on_failure: bool = False,
    ) -> List[WaveResult]:
        """Execute waves sequentially, parallelizing within each wave.

        Args:
            waves: List of waves, where each wave is a list of delegations.
            execute_fn: Function to execute a single delegation.
            timeout_s: Optional timeout per wave.
            stop_on_failure: If True, stop execution if any delegation fails.

        Returns:
            List of WaveResults for each executed wave.
        """
        wave_results: List[WaveResult] = []

        for i, wave in enumerate(waves):
            results = self.execute_parallel(wave, execute_fn, timeout_s)

            wave_result = WaveResult(
                wave_index=i,
                results=results,
            )
            wave_results.append(wave_result)

            # Check for failures if stop_on_failure is enabled
            if stop_on_failure and not wave_result.all_successful:
                break

        return wave_results

    def cancel_delegation(self, delegation_id: str) -> bool:
        """Attempt to cancel a running delegation.

        Args:
            delegation_id: ID of the delegation to cancel.

        Returns:
            True if cancellation was requested, False if not found.
        """
        cancelled = False
        with self._lock:
            future = self._active_futures.get(delegation_id)
            if future:
                cancelled = future.cancel()
        return cancelled

    def cancel_all(self) -> int:
        """Cancel all running delegations.

        Returns:
            Number of delegations for which cancellation was requested.
        """
        cancelled = 0
        with self._lock:
            for future in self._active_futures.values():
                if future.cancel():
                    cancelled += 1
            self._active_futures.clear()
        return cancelled

    def get_active_delegations(self) -> List[str]:
        """Get IDs of currently executing delegations.

        Returns:
            List of delegation IDs that are currently running.
        """
        with self._lock:
            return list(self._active_futures.keys())

    @property
    def max_workers(self) -> int:
        """Get the maximum number of concurrent workers."""
        return self._max_workers

    @property
    def default_timeout_s(self) -> float:
        """Get the default timeout in seconds."""
        return self._default_timeout_s
