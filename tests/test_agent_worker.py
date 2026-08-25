from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event
import time

from kairospy.strategy.apps.agent.application import (
    AgentApplication,
    AgentMode,
    DecisionKind,
    DecisionReceipt,
    DecisionResult,
    DecisionStatus,
    IntentCandidate,
    ReduceTargetQuantity,
    AgentEventStatus,
)
from kairospy.strategy.apps.agent.application.policy import DecisionPolicy
from kairospy.strategy.apps.agent.services import (
    AgentDecisionWorker,
    DecisionRecordStore,
    DecisionTask,
)
from kairospy.strategy.apps.agent.services.events import AgentEventStream
from kairospy.investment.apps.execution.application import (
    ImmediateAlgorithm,
    TargetPositionRequest,
)
from kairospy.strategy import CommandResult


class Runtime:
    def __init__(self, result: DecisionResult) -> None:
        self.result = result
        self.calls: list[IntentCandidate] = []

    def decide(self, candidate: IntentCandidate) -> DecisionResult:
        self.calls.append(candidate)
        return self.result


class BlockingRuntime(Runtime):
    def __init__(self, result: DecisionResult) -> None:
        super().__init__(result)
        self.entered = Event()
        self.release = Event()

    def decide(self, candidate: IntentCandidate) -> DecisionResult:
        self.calls.append(candidate)
        self.entered.set()
        self.release.wait(2)
        return self.result


class FailingRuntime:
    def decide(self, candidate: IntentCandidate) -> DecisionResult:
        raise TimeoutError("model timed out")


class InvalidRuntime:
    def decide(self, candidate: IntentCandidate) -> object:
        return {"decision": "approve"}


def _candidate(
    index: int,
    *,
    mode: AgentMode = AgentMode.GATE,
) -> IntentCandidate:
    now = datetime.now(timezone.utc)
    agent = AgentApplication(enabled=True, initial_mode=mode)
    return IntentCandidate(
        decision_id=f"decision-{index}",
        request_id=f"request-{index}",
        intent_id=f"intent-{index}",
        workspace_id="workspace",
        strategy_id="strategy",
        launch_id="launch",
        instance_id="instance",
        operation="target_position",
        request=TargetPositionRequest(
            "BTCUSDT",
            Decimal("2"),
            algorithm=ImmediateAlgorithm(),
            account_id="main",
            intent_id=f"intent-{index}",
        ),
        exposure_effect="unknown",
        profile_hash="profile-hash",
        snapshot=agent._snapshot("execution.intent_review", now=now),
        submitted_at=now,
        deadline=now + timedelta(seconds=5),
    )


def _worker(
    tmp_path: Path,
    runtime: object,
    *,
    capacity: int = 4,
) -> tuple[AgentDecisionWorker, DecisionRecordStore]:
    records = DecisionRecordStore(tmp_path / "decisions.sqlite3")
    worker = AgentDecisionWorker(
        runtime=runtime,
        policy=DecisionPolicy({"allow_quantity_reduction": True}),
        records=records,
        queue_capacity=capacity,
    )
    worker.start()
    return worker, records


def test_gate_approve_submits_original_after_persisted_run(tmp_path: Path) -> None:
    runtime = Runtime(DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved"))
    worker, records = _worker(tmp_path, runtime)
    candidate = _candidate(1)
    submissions: list[object] = []

    pending = worker.submit(
        DecisionTask(
            candidate,
            lambda request: _accepted(candidate.request_id, request, submissions),
        )
    )
    finished = _wait_terminal(records, candidate.decision_id)

    assert pending.status is DecisionStatus.PENDING
    assert finished.status is DecisionStatus.APPROVED
    assert finished.delivery_certainty == "sent"
    assert submissions == [candidate.request]
    worker.close(timeout=1)


def test_worker_publishes_one_minimal_terminal_notice(tmp_path: Path) -> None:
    runtime = Runtime(
        DecisionResult(
            DecisionKind.REJECT,
            9000,
            ("risk_too_high",),
            (),
            "private model summary",
        )
    )
    records = DecisionRecordStore(tmp_path / "decisions.sqlite3")
    published: list[tuple[IntentCandidate, DecisionReceipt, tuple[str, ...]]] = []
    worker = AgentDecisionWorker(
        runtime=runtime,
        policy=DecisionPolicy(
            {"allow_quantity_reduction": True},
            reason_codes=("risk_too_high",),
        ),
        records=records,
        queue_capacity=4,
        publish_terminal=lambda candidate, receipt, reason_codes: published.append(
            (candidate, receipt, reason_codes)
        ),
    )
    worker.start()
    candidate = _candidate(10)

    worker.submit(
        DecisionTask(
            candidate,
            lambda request: CommandResult(candidate.request_id, "accepted"),
        )
    )
    _wait_terminal(records, candidate.decision_id)
    deadline = time.monotonic() + 1
    while not published and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(published) == 1
    assert published[0][0] is candidate
    assert published[0][1].status.value == AgentEventStatus.REJECTED.value
    assert published[0][2] == ("risk_too_high",)
    worker.close(timeout=1)


def test_fixture_notice_uses_candidate_time_not_wall_clock() -> None:
    submitted_at = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)
    candidate = replace(
        _candidate(11),
        runtime="fixture",
        submitted_at=submitted_at,
        deadline=datetime.max.replace(tzinfo=timezone.utc),
    )
    stream = AgentEventStream(enabled=True)

    stream.publish(
        candidate,
        DecisionReceipt(
            candidate.decision_id,
            candidate.request_id,
            candidate.intent_id,
            DecisionStatus.REJECTED,
        ),
        (),
    )

    (event,) = stream.drain()
    assert event.metadata.occurred_at == submitted_at
    assert event.metadata.occurred_at_unix_nanos == 1_717_243_200_000_000_000


def test_agent_notice_failure_cannot_roll_back_execution_submission(
    tmp_path: Path,
) -> None:
    runtime = Runtime(DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved"))
    records = DecisionRecordStore(tmp_path / "decisions.sqlite3")
    submissions: list[object] = []

    def fail_notice(candidate, receipt, reason_codes) -> None:
        assert submissions == [candidate.request]
        raise RuntimeError("strategy notification unavailable")

    worker = AgentDecisionWorker(
        runtime=runtime,
        policy=DecisionPolicy({"allow_quantity_reduction": True}),
        records=records,
        queue_capacity=4,
        publish_terminal=fail_notice,
    )
    worker.start()
    candidate = _candidate(11)
    worker.submit(
        DecisionTask(
            candidate,
            lambda request: _accepted(candidate.request_id, request, submissions),
        )
    )

    terminal = _wait_terminal(records, candidate.decision_id)

    assert terminal.status is DecisionStatus.APPROVED
    assert terminal.delivery_certainty == "sent"
    assert submissions == [candidate.request]
    worker.close(timeout=1)


def test_agent_notice_does_not_publish_profile_rejected_reason_codes(
    tmp_path: Path,
) -> None:
    runtime = Runtime(
        DecisionResult(
            DecisionKind.APPROVE,
            9000,
            ("model_invented_code",),
            (),
            "not allowlisted",
        )
    )
    records = DecisionRecordStore(tmp_path / "decisions.sqlite3")
    published: list[tuple[DecisionReceipt, tuple[str, ...]]] = []
    worker = AgentDecisionWorker(
        runtime=runtime,
        policy=DecisionPolicy({}, reason_codes=()),
        records=records,
        queue_capacity=4,
        publish_terminal=lambda candidate, receipt, reason_codes: published.append(
            (receipt, reason_codes)
        ),
    )
    worker.start()
    candidate = _candidate(12)
    worker.submit(
        DecisionTask(
            candidate,
            lambda request: CommandResult(candidate.request_id, "accepted"),
        )
    )
    terminal = _wait_terminal(records, candidate.decision_id)
    assert worker.wait_idle(timeout=1)

    assert terminal.status is DecisionStatus.ABSTAINED
    assert published == [(terminal, ())]
    worker.close(timeout=1)


def test_reject_does_not_submit_and_revise_submits_effective(tmp_path: Path) -> None:
    rejected_runtime = Runtime(
        DecisionResult(DecisionKind.REJECT, 9000, (), (), "risk")
    )
    rejected_worker, rejected_records = _worker(tmp_path / "reject", rejected_runtime)
    rejected_candidate = _candidate(1)
    rejected_submissions: list[object] = []
    rejected_worker.submit(
        DecisionTask(
            rejected_candidate,
            lambda request: _accepted(
                rejected_candidate.request_id, request, rejected_submissions
            ),
        )
    )
    rejected = _wait_terminal(rejected_records, rejected_candidate.decision_id)
    assert rejected.status is DecisionStatus.REJECTED
    assert rejected.delivery_certainty == "not_sent"
    assert rejected_submissions == []
    assert rejected_worker.health()["last_success_at"] is not None
    assert rejected_worker.health()["rolling_error_rate"] == 0.0
    rejected_worker.close(timeout=1)

    revised_runtime = Runtime(
        DecisionResult(
            DecisionKind.REVISE,
            8500,
            (),
            (),
            "smaller",
            (ReduceTargetQuantity("1"),),
        )
    )
    revised_worker, revised_records = _worker(tmp_path / "revise", revised_runtime)
    revised_candidate = _candidate(2, mode=AgentMode.REVISE)
    revised_submissions: list[object] = []
    revised_worker.submit(
        DecisionTask(
            revised_candidate,
            lambda request: _accepted(
                revised_candidate.request_id, request, revised_submissions
            ),
        )
    )
    revised = _wait_terminal(revised_records, revised_candidate.decision_id)
    assert revised.status is DecisionStatus.REVISED
    effective = revised_submissions[0]
    assert isinstance(effective, TargetPositionRequest)
    assert effective.quantity == Decimal("1")
    revised_worker.close(timeout=1)


def test_gate_treats_revision_as_abstain_and_fails_closed(tmp_path: Path) -> None:
    runtime = Runtime(
        DecisionResult(
            DecisionKind.REVISE,
            8000,
            (),
            (),
            "smaller",
            (ReduceTargetQuantity("1"),),
        )
    )
    worker, records = _worker(tmp_path, runtime)
    candidate = _candidate(1, mode=AgentMode.GATE)
    submissions: list[object] = []
    worker.submit(
        DecisionTask(
            candidate,
            lambda request: _accepted(candidate.request_id, request, submissions),
        )
    )
    finished = _wait_terminal(records, candidate.decision_id)
    assert finished.status is DecisionStatus.ABSTAINED
    assert submissions == []
    worker.close(timeout=1)


def test_shadow_records_result_without_resubmitting(tmp_path: Path) -> None:
    runtime = Runtime(DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved"))
    worker, records = _worker(tmp_path, runtime)
    candidate = _candidate(1, mode=AgentMode.SHADOW)
    submissions: list[object] = []
    shadow_result = CommandResult(
        candidate.request_id, "accepted", {"intent_id": candidate.intent_id}
    )
    worker.submit(
        DecisionTask(
            candidate,
            lambda request: _accepted(candidate.request_id, request, submissions),
            shadow_result,
        )
    )
    finished = _wait_terminal(records, candidate.decision_id)
    assert finished.status is DecisionStatus.APPROVED
    assert finished.final_submission_status == "accepted"
    assert submissions == []
    worker.close(timeout=1)


def test_queue_full_is_non_blocking_and_persisted(tmp_path: Path) -> None:
    runtime = BlockingRuntime(
        DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved")
    )
    worker, records = _worker(tmp_path, runtime, capacity=1)

    def submit(request: object) -> CommandResult:
        return CommandResult("request", "accepted")

    first = _candidate(1)
    second = _candidate(2)
    third = _candidate(3)
    worker.submit(DecisionTask(first, submit))
    assert runtime.entered.wait(1)
    worker.submit(DecisionTask(second, submit))

    started = time.monotonic()
    full = worker.submit(DecisionTask(third, submit))
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert full.status is DecisionStatus.FAILED
    assert full.delivery_certainty == "not_sent"
    assert "queue is full" in (full.reason or "")
    assert worker.health()["last_failure"] == "Decision worker queue is full"
    error_rate = worker.health()["rolling_error_rate"]
    assert isinstance(error_rate, (int, float))
    assert error_rate > 0
    runtime.release.set()
    _wait_terminal(records, first.decision_id)
    _wait_terminal(records, second.decision_id)
    worker.close(timeout=1)


def test_queue_full_bypasses_agent_only_for_proven_reduction(tmp_path: Path) -> None:
    runtime = BlockingRuntime(
        DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved")
    )
    worker, records = _worker(tmp_path, runtime, capacity=1)
    first = _candidate(1)
    second = _candidate(2)
    reduction = replace(_candidate(3), exposure_effect="reduce")
    submissions: list[object] = []

    def no_op_submit(request: object) -> CommandResult:
        return CommandResult("request", "accepted")

    worker.submit(DecisionTask(first, no_op_submit))
    assert runtime.entered.wait(1)
    worker.submit(DecisionTask(second, no_op_submit))
    bypassed = worker.submit(
        DecisionTask(
            reduction,
            no_op_submit,
            bypass=lambda request: _accepted(
                reduction.request_id, request, submissions
            ),
        )
    )

    assert bypassed.status is DecisionStatus.ABSTAINED
    assert bypassed.delivery_certainty == "sent"
    assert bypassed.final_submission_status == "accepted"
    assert submissions == [reduction.request]
    runtime.release.set()
    _wait_terminal(records, first.decision_id)
    _wait_terminal(records, second.decision_id)
    worker.close(timeout=1)


def test_shadow_queue_full_reduction_is_not_submitted_twice(tmp_path: Path) -> None:
    runtime = BlockingRuntime(
        DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved")
    )
    worker, records = _worker(tmp_path, runtime, capacity=1)
    submissions: list[object] = []
    first = replace(_candidate(31, mode=AgentMode.SHADOW), exposure_effect="reduce")
    second = replace(_candidate(32, mode=AgentMode.SHADOW), exposure_effect="reduce")
    queue_full = replace(
        _candidate(33, mode=AgentMode.SHADOW), exposure_effect="reduce"
    )

    worker.submit_shadow(
        first,
        lambda request: _accepted(first.request_id, request, submissions),
    )
    assert runtime.entered.wait(1)
    worker.submit_shadow(
        second,
        lambda request: _accepted(second.request_id, request, submissions),
    )
    submitted = worker.submit_shadow(
        queue_full,
        lambda request: _accepted(queue_full.request_id, request, submissions),
    )

    terminal = records.decision(queue_full.decision_id)
    assert submitted.status == "accepted"
    assert terminal is not None
    assert terminal.status is DecisionStatus.FAILED
    assert terminal.delivery_certainty == "sent"
    assert submissions == [first.request, second.request, queue_full.request]
    runtime.release.set()
    assert worker.wait_idle(timeout=1)
    worker.close(timeout=1)


def test_runtime_failure_bypasses_agent_for_proven_reduction(tmp_path: Path) -> None:
    worker, records = _worker(tmp_path, FailingRuntime())
    original = _candidate(1)
    candidate = replace(original, exposure_effect="reduce")
    submissions: list[object] = []
    worker.submit(
        DecisionTask(
            candidate,
            lambda request: _accepted(candidate.request_id, request, submissions),
            bypass=lambda request: _accepted(
                candidate.request_id, request, submissions
            ),
        )
    )

    finished = _wait_terminal(records, candidate.decision_id)

    assert finished.status is DecisionStatus.ABSTAINED
    assert finished.delivery_certainty == "sent"
    assert submissions == [candidate.request]
    assert "Agent runtime failed" in str(worker.health()["last_failure"])
    assert worker.health()["rolling_error_rate"] == 1.0
    worker.close(timeout=1)


def test_stale_candidate_and_invalid_runtime_output_fail_closed(tmp_path: Path) -> None:
    runtime = Runtime(DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved"))
    stale_worker, stale_records = _worker(tmp_path / "stale", runtime)
    now = datetime.now(timezone.utc)
    stale = replace(
        _candidate(40),
        submitted_at=now - timedelta(seconds=2),
        deadline=now - timedelta(seconds=1),
    )
    stale_worker.submit(
        DecisionTask(stale, lambda request: CommandResult("request", "accepted"))
    )
    stale_receipt = _wait_terminal(stale_records, stale.decision_id)

    assert stale_receipt.status is DecisionStatus.FAILED
    assert runtime.calls == []
    stale_worker.close(timeout=1)

    invalid_worker, invalid_records = _worker(tmp_path / "invalid", InvalidRuntime())
    invalid = _candidate(41)
    invalid_worker.submit(
        DecisionTask(invalid, lambda request: CommandResult("request", "accepted"))
    )
    invalid_receipt = _wait_terminal(invalid_records, invalid.decision_id)

    assert invalid_receipt.status is DecisionStatus.FAILED
    assert invalid_receipt.delivery_certainty == "not_sent"
    invalid_worker.close(timeout=1)


def test_indeterminate_final_submission_is_never_retried(tmp_path: Path) -> None:
    runtime = Runtime(DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved"))
    worker, records = _worker(tmp_path, runtime)
    candidate = _candidate(42)
    attempts = 0

    def uncertain_submit(request: object) -> CommandResult:
        nonlocal attempts
        attempts += 1
        raise TimeoutError("delivery certainty unknown")

    task = DecisionTask(candidate, uncertain_submit)
    worker.submit(task)
    terminal = _wait_terminal(records, candidate.decision_id)
    duplicate = worker.submit(task)

    assert terminal.status is DecisionStatus.SUBMISSION_INDETERMINATE
    assert terminal.delivery_certainty == "indeterminate"
    assert duplicate.status is DecisionStatus.SUBMISSION_INDETERMINATE
    assert attempts == 1
    worker.close(timeout=1)


def test_shutdown_is_bounded_and_does_not_submit_late_gate_result(
    tmp_path: Path,
) -> None:
    runtime = BlockingRuntime(
        DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved")
    )
    worker, records = _worker(tmp_path, runtime)
    candidate = _candidate(1)
    submissions: list[object] = []
    worker.submit(
        DecisionTask(
            candidate,
            lambda request: _accepted(candidate.request_id, request, submissions),
        )
    )
    assert runtime.entered.wait(1)

    worker.close(timeout=0)
    assert worker.health()["last_failure"] == "shutdown_timeout"
    runtime.release.set()
    finished = _wait_terminal(records, candidate.decision_id)
    worker.close(timeout=1)

    assert finished.status is DecisionStatus.INTERRUPTED
    assert finished.delivery_certainty == "not_sent"
    assert submissions == []


def test_fixture_barrier_waits_until_worker_is_idle(tmp_path: Path) -> None:
    runtime = BlockingRuntime(
        DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved")
    )
    worker, records = _worker(tmp_path, runtime)
    candidate = _candidate(20)
    worker.submit(
        DecisionTask(
            candidate,
            lambda request: CommandResult(candidate.request_id, "accepted"),
        )
    )
    assert runtime.entered.wait(1)
    assert worker.wait_idle(timeout=0.01) is False

    runtime.release.set()
    assert worker.wait_idle(timeout=1) is True
    assert (
        _wait_terminal(records, candidate.decision_id).status is DecisionStatus.APPROVED
    )
    worker.close(timeout=1)


def _accepted(
    request_id: str, request: object, submissions: list[object]
) -> CommandResult:
    submissions.append(request)
    return CommandResult(request_id, "accepted", {"intent_id": "intent"})


def _wait_terminal(records: DecisionRecordStore, decision_id: str):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        receipt = records.decision(decision_id)
        if receipt is not None and receipt.status not in {
            DecisionStatus.PENDING,
            DecisionStatus.RUNNING,
            DecisionStatus.SUBMITTING,
        }:
            return receipt
        time.sleep(0.01)
    raise AssertionError(f"Decision did not complete: {decision_id}")
