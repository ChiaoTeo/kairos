from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from datetime import datetime, timezone
import logging
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
import time
from typing import Callable

from kairospy.strategy.results import CommandResult

from ..models import (
    AgentMode,
    DecisionKind,
    DecisionReceipt,
    DecisionResult,
    DecisionRuntimeOutput,
    DecisionStatus,
    IntentCandidate,
    ToolEvidence,
)
from ..policy import DecisionPolicy
from .records import DecisionRecordStore


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DecisionTask:
    candidate: IntentCandidate
    submit: Callable[[object], CommandResult]
    shadow_result: CommandResult | None = None


class AgentDecisionWorker:
    """Single-owner bounded Decision run worker."""

    def __init__(
        self,
        *,
        runtime: object,
        policy: DecisionPolicy,
        records: DecisionRecordStore,
        queue_capacity: int,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("Agent worker queue_capacity must be positive")
        self._runtime = runtime
        self._policy = policy
        self._records = records
        self._queue: Queue[DecisionTask | None] = Queue(maxsize=queue_capacity)
        self._lock = Lock()
        self._thread: Thread | None = None
        self._stop_requested = Event()
        self._accepting = False
        self._in_flight = 0
        self._last_success_at: datetime | None = None
        self._last_failure: str | None = None
        self._latencies_millis: deque[float] = deque(maxlen=1_000)
        self._recent_errors: deque[bool] = deque(maxlen=1_000)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._records.interrupt_nonterminal()
            self._stop_requested.clear()
            self._accepting = True
            self._thread = Thread(
                target=self._run,
                name="kairos-decision-agent",
                daemon=True,
            )
            self._thread.start()

    def submit(self, task: DecisionTask) -> DecisionReceipt:
        receipt, created = self._records.admit(task.candidate)
        if not created:
            return receipt
        return self._enqueue(task)

    def submit_shadow(
        self,
        candidate: IntentCandidate,
        submit_original: Callable[[object], CommandResult],
    ) -> CommandResult:
        """Persist first, submit original, then enqueue non-authoritative review."""

        receipt, created = self._records.admit(candidate)
        if not created:
            return CommandResult(
                candidate.request_id,
                "duplicate",
                {"intent_id": candidate.intent_id, "decision_id": receipt.decision_id},
            )
        try:
            submitted = submit_original(candidate.request)
        except Exception as error:
            self._records.finish(
                candidate.decision_id,
                DecisionStatus.SUBMISSION_INDETERMINATE,
                delivery_certainty="indeterminate",
                reason=f"{type(error).__name__}: Execution submission failed",
            )
            raise
        queued = self._enqueue(
            DecisionTask(candidate, submit_original, submitted),
            failure_certainty="sent",
            final_submission_status=submitted.status,
        )
        if queued.status is DecisionStatus.FAILED:
            return submitted
        return submitted

    def _enqueue(
        self,
        task: DecisionTask,
        *,
        failure_certainty: str = "not_sent",
        final_submission_status: str | None = None,
    ) -> DecisionReceipt:
        if not self._accepting:
            return self._records.finish(
                task.candidate.decision_id,
                DecisionStatus.INTERRUPTED,
                final_submission_status=final_submission_status,
                delivery_certainty=failure_certainty,
                reason="Decision worker is not accepting candidates",
            )
        try:
            self._queue.put_nowait(task)
        except Full:
            return self._records.finish(
                task.candidate.decision_id,
                DecisionStatus.FAILED,
                final_submission_status=final_submission_status,
                delivery_certainty=failure_certainty,
                reason="Decision worker queue is full",
            )
        receipt = self._records.decision(task.candidate.decision_id)
        if receipt is None:
            raise RuntimeError("Decision record disappeared after queue admission")
        return receipt

    def health(self) -> dict[str, object]:
        with self._lock:
            thread = self._thread
            state = (
                "stopped"
                if thread is None
                else "ready"
                if thread.is_alive() and self._accepting
                else "stopping"
            )
            latencies = sorted(self._latencies_millis)
            errors = tuple(self._recent_errors)
            return {
                "state": state,
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "in_flight": self._in_flight,
                "last_success_at": self._last_success_at,
                "last_failure": self._last_failure,
                "rolling_error_rate": (sum(errors) / len(errors) if errors else 0.0),
                "latency_p50_millis": _percentile(latencies, 0.50),
                "latency_p95_millis": _percentile(latencies, 0.95),
                "latency_p99_millis": _percentile(latencies, 0.99),
            }

    def close(self, *, timeout: float) -> None:
        if timeout < 0:
            raise ValueError("Agent worker shutdown timeout cannot be negative")
        with self._lock:
            self._accepting = False
            thread = self._thread
            self._stop_requested.set()
        if thread is None:
            self._records.close()
            return
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        thread.join(timeout)
        self._interrupt_queued()
        if thread.is_alive():
            with self._lock:
                self._last_failure = "shutdown_timeout"
            return
        close = getattr(self._runtime, "close", None)
        if callable(close):
            close()
        self._records.close()
        with self._lock:
            self._thread = None

    def _run(self) -> None:
        while not self._stop_requested.is_set():
            try:
                task = self._queue.get(timeout=0.1)
            except Empty:
                continue
            if task is None:
                self._queue.task_done()
                return
            with self._lock:
                self._in_flight += 1
            started = time.monotonic()
            try:
                self._execute(task)
            except Exception as error:
                self._last_failure = type(error).__name__
                try:
                    self._records.finish(
                        task.candidate.decision_id,
                        DecisionStatus.FAILED,
                        delivery_certainty="not_sent",
                        reason=f"{type(error).__name__}: Decision worker failed",
                    )
                except RuntimeError:
                    pass
            finally:
                elapsed = (time.monotonic() - started) * 1_000
                receipt = self._records.decision(task.candidate.decision_id)
                failed = receipt is None or receipt.status in {
                    DecisionStatus.FAILED,
                    DecisionStatus.INTERRUPTED,
                    DecisionStatus.SUBMISSION_INDETERMINATE,
                }
                with self._lock:
                    self._in_flight -= 1
                    self._latencies_millis.append(elapsed)
                    self._recent_errors.append(failed)
                _LOG.info(
                    "Decision Agent run completed",
                    extra={
                        "decision_id": task.candidate.decision_id,
                        "request_id": task.candidate.request_id,
                        "intent_id": task.candidate.intent_id,
                        "launch_id": task.candidate.launch_id,
                        "instance_id": task.candidate.instance_id,
                        "strategy_id": task.candidate.strategy_id,
                        "capability": task.candidate.snapshot.capability,
                        "agent_mode": task.candidate.snapshot.mode.value,
                        "mode_revision": task.candidate.snapshot.mode_revision,
                        "outcome": None if receipt is None else receipt.status.value,
                        "latency_millis": elapsed,
                    },
                )
                self._queue.task_done()

    def _execute(self, task: DecisionTask) -> None:
        candidate = task.candidate
        self._records.mark_running(candidate.decision_id)
        if datetime.now(timezone.utc) >= candidate.deadline:
            self._fail_or_bypass(task, "Decision deadline expired before model run")
            return
        decide = getattr(self._runtime, "decide", None)
        if not callable(decide):
            raise TypeError("Decision runtime does not provide decide(candidate)")
        try:
            runtime_output = decide(candidate)
        except Exception as error:
            self._fail_or_bypass(task, f"{type(error).__name__}: Agent runtime failed")
            return
        if isinstance(runtime_output, DecisionResult):
            result = runtime_output
            tool_evidence: tuple[ToolEvidence, ...] = ()
        elif isinstance(runtime_output, DecisionRuntimeOutput):
            result = runtime_output.result
            tool_evidence = runtime_output.tool_evidence
        else:
            self._fail_or_bypass(
                task, "Decision runtime returned an invalid result type"
            )
            return
        if datetime.now(timezone.utc) >= candidate.deadline:
            self._fail_or_bypass(
                task,
                "Decision deadline expired after model run",
                result=result,
                tool_evidence=tool_evidence,
            )
            return
        if (
            self._stop_requested.is_set()
            and candidate.snapshot.mode is not AgentMode.SHADOW
        ):
            self._records.finish(
                candidate.decision_id,
                DecisionStatus.INTERRUPTED,
                result=result,
                tool_evidence=tool_evidence,
                delivery_certainty="not_sent",
                reason="Decision worker stopped before final submission",
            )
            return
        outcome = self._policy.apply(candidate, result)
        if candidate.snapshot.mode is AgentMode.SHADOW:
            self._finish_shadow(task, result, outcome.decision, tool_evidence)
            return
        decision = outcome.decision
        effective = outcome.effective_request
        if (
            candidate.snapshot.mode is AgentMode.GATE
            and decision is DecisionKind.REVISE
        ):
            decision = DecisionKind.ABSTAIN
            effective = None
        if decision is DecisionKind.ABSTAIN and candidate.exposure_effect == "reduce":
            effective = candidate.request
        if (
            decision in {DecisionKind.REJECT, DecisionKind.ABSTAIN}
            and effective is None
        ):
            self._records.finish(
                candidate.decision_id,
                _terminal_status(decision),
                result=result,
                tool_evidence=tool_evidence,
                delivery_certainty="not_sent",
                reason=outcome.reason,
            )
            return
        if effective is None:
            raise RuntimeError("Approved Decision is missing an effective request")
        self._records.mark_submitting(candidate.decision_id)
        try:
            submitted = task.submit(effective)
        except Exception as error:
            self._records.finish(
                candidate.decision_id,
                DecisionStatus.SUBMISSION_INDETERMINATE,
                result=result,
                tool_evidence=tool_evidence,
                effective_request=effective,
                delivery_certainty="indeterminate",
                reason=f"{type(error).__name__}: Execution submission failed",
            )
            return
        certainty = "sent"
        status = _terminal_status(decision)
        self._records.finish(
            candidate.decision_id,
            status,
            result=result,
            tool_evidence=tool_evidence,
            effective_request=effective,
            final_submission_status=submitted.status,
            delivery_certainty=certainty,
            reason=submitted.error,
        )
        self._last_success_at = datetime.now(timezone.utc)

    def _fail_or_bypass(
        self,
        task: DecisionTask,
        reason: str,
        *,
        result: DecisionResult | None = None,
        tool_evidence: tuple[ToolEvidence, ...] = (),
    ) -> None:
        candidate = task.candidate
        if (
            self._stop_requested.is_set()
            and candidate.snapshot.mode is not AgentMode.SHADOW
        ):
            self._records.finish(
                candidate.decision_id,
                DecisionStatus.INTERRUPTED,
                result=result,
                tool_evidence=tool_evidence,
                delivery_certainty="not_sent",
                reason="Decision worker stopped before final submission",
            )
            return
        if candidate.snapshot.mode is AgentMode.SHADOW:
            submitted = task.shadow_result
            self._records.finish(
                candidate.decision_id,
                DecisionStatus.FAILED,
                result=result,
                tool_evidence=tool_evidence,
                final_submission_status=None if submitted is None else submitted.status,
                delivery_certainty=None if submitted is None else "sent",
                reason=reason,
            )
            return
        if candidate.exposure_effect != "reduce":
            self._records.finish(
                candidate.decision_id,
                DecisionStatus.FAILED,
                result=result,
                tool_evidence=tool_evidence,
                delivery_certainty="not_sent",
                reason=reason,
            )
            return
        try:
            self._records.mark_submitting(candidate.decision_id)
            submitted = task.submit(candidate.request)
        except Exception as error:
            self._records.finish(
                candidate.decision_id,
                DecisionStatus.SUBMISSION_INDETERMINATE,
                result=result,
                tool_evidence=tool_evidence,
                effective_request=candidate.request,
                delivery_certainty="indeterminate",
                reason=f"{type(error).__name__}: Execution submission failed",
            )
            return
        self._records.finish(
            candidate.decision_id,
            DecisionStatus.ABSTAINED,
            result=result,
            tool_evidence=tool_evidence,
            effective_request=candidate.request,
            final_submission_status=submitted.status,
            delivery_certainty="sent",
            reason=reason,
        )

    def _finish_shadow(
        self,
        task: DecisionTask,
        result: DecisionResult,
        decision: DecisionKind,
        tool_evidence: tuple[ToolEvidence, ...],
    ) -> None:
        submitted = task.shadow_result
        self._records.finish(
            task.candidate.decision_id,
            _terminal_status(decision),
            result=result,
            tool_evidence=tool_evidence,
            effective_request=None,
            final_submission_status=None if submitted is None else submitted.status,
            delivery_certainty=None if submitted is None else "sent",
            reason=None if submitted is None else submitted.error,
        )
        self._last_success_at = datetime.now(timezone.utc)

    def _interrupt_queued(self) -> None:
        while True:
            try:
                task = self._queue.get_nowait()
            except Empty:
                return
            if task is not None:
                try:
                    self._records.finish(
                        task.candidate.decision_id,
                        DecisionStatus.INTERRUPTED,
                        delivery_certainty="not_sent",
                        reason="Decision worker stopped before run",
                    )
                except RuntimeError:
                    pass
            self._queue.task_done()


def _terminal_status(decision: DecisionKind) -> DecisionStatus:
    return {
        DecisionKind.APPROVE: DecisionStatus.APPROVED,
        DecisionKind.REVISE: DecisionStatus.REVISED,
        DecisionKind.REJECT: DecisionStatus.REJECTED,
        DecisionKind.ABSTAIN: DecisionStatus.ABSTAINED,
    }[decision]


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, max(0, round((len(values) - 1) * fraction)))
    return values[index]


__all__ = ["AgentDecisionWorker", "DecisionTask"]
