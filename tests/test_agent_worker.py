from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event
import time

from kairospy.application.agent import (
    AgentApplication,
    AgentMode,
    DecisionKind,
    DecisionResult,
    DecisionStatus,
    IntentCandidate,
    ReduceTargetQuantity,
)
from kairospy.application.agent.policy import DecisionPolicy
from kairospy.application.agent.services import (
    AgentDecisionWorker,
    DecisionRecordStore,
    DecisionTask,
)
from kairospy.application.execution import TargetPositionRequest
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
        strategy_id="strategy",
        launch_id="launch",
        instance_id="instance",
        operation="target_position",
        request=TargetPositionRequest(
            "BTCUSDT", Decimal("2"), account_id="main", intent_id=f"intent-{index}"
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
    runtime.release.set()
    _wait_terminal(records, first.decision_id)
    _wait_terminal(records, second.decision_id)
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
        )
    )

    finished = _wait_terminal(records, candidate.decision_id)

    assert finished.status is DecisionStatus.ABSTAINED
    assert finished.delivery_certainty == "sent"
    assert submissions == [candidate.request]
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
