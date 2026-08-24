from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event
import time

from kairospy.application.agent import (
    AgentApplication,
    AgentContextDocument,
    AgentMode,
    DecisionKind,
    DecisionResult,
    DecisionStatus,
    ReduceTargetQuantity,
)
from kairospy.application.agent.policy import DecisionPolicy
from kairospy.application.agent.services import (
    AgentDecisionWorker,
    DecisionRecordStore,
)
from kairospy.application.agent.services.controlled_execution import (
    AgentControlledExecutionCommands,
    UnavailableAgentExecutionCommands,
)
from kairospy.application.execution import (
    DeliveryCertainty,
    ExecutionApplication,
    SubmissionStatus,
    TargetPositionRequest,
)
from kairospy.primitives.reference import InstrumentId
from kairospy.strategy import CommandResult


class Runtime:
    def __init__(self, result: DecisionResult) -> None:
        self.result = result

    def decide(self, candidate):
        return self.result


class BlockingRuntime(Runtime):
    def __init__(self, result: DecisionResult) -> None:
        super().__init__(result)
        self.entered = Event()
        self.release = Event()

    def decide(self, candidate):
        self.entered.set()
        self.release.wait(2)
        return self.result


class FailingRuntime:
    def decide(self, candidate):
        raise TimeoutError("model timed out")


class Commands:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    def target_position(self, request, **identity) -> CommandResult:
        self.calls.append((request, identity))
        return CommandResult(
            identity["request_id"], "accepted", {"intent_id": request.intent_id}
        )


def _assembled(
    tmp_path: Path,
    *,
    mode: AgentMode,
    result: DecisionResult,
    required_contexts: tuple[str, ...] = (),
    exposure: str = "unknown",
    runtime: object | None = None,
    queue_capacity: int = 8,
):
    agent = AgentApplication(enabled=True, initial_mode=mode)
    records = DecisionRecordStore(tmp_path / "decisions.sqlite3")
    worker = AgentDecisionWorker(
        runtime=runtime or Runtime(result),
        policy=DecisionPolicy({"allow_quantity_reduction": True}),
        records=records,
        queue_capacity=queue_capacity,
    )
    worker.start()
    commands = Commands()
    controlled = AgentControlledExecutionCommands(
        commands,
        agent=agent,
        worker=worker,
        workspace_id="workspace",
        launch_id="launch",
        profile_hash="profile-hash",
        runtime="fixture",
        model=None,
        tool_profiles=(),
        operations=("target_position",),
        required_contexts=required_contexts,
        max_decision_age_seconds=5,
        classify_exposure=lambda request: exposure,
    )
    application = ExecutionApplication(
        controlled,
        None,
        strategy_id="strategy",
        instance_id="instance",
    )
    return application, agent, commands, worker, records


def test_gate_returns_pending_not_sent_then_worker_submits(tmp_path: Path) -> None:
    application, _, commands, worker, records = _assembled(
        tmp_path,
        mode=AgentMode.GATE,
        result=DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved"),
    )

    receipt = application.target_position(
        InstrumentId("BTCUSDT"), Decimal("1"), account="main"
    )
    terminal = _wait_any_terminal(records)

    assert receipt.status is SubmissionStatus.PENDING
    assert receipt.delivery_certainty is DeliveryCertainty.NOT_SENT
    assert receipt.intent_id is not None
    assert terminal.status is DecisionStatus.APPROVED
    assert len(commands.calls) == 1
    submitted = commands.calls[0][0]
    assert getattr(submitted, "intent_id") == str(receipt.intent_id)
    worker.close(timeout=1)


def test_fixture_candidate_uses_deterministic_time_not_wall_clock(
    tmp_path: Path,
) -> None:
    application, _, _, worker, records = _assembled(
        tmp_path,
        mode=AgentMode.GATE,
        result=DecisionResult(DecisionKind.REJECT, 9000, (), (), "rejected"),
    )

    application.target_position(InstrumentId("BTCUSDT"), Decimal("1"), account="main")
    _wait_any_terminal(records)
    row = records._connection.execute(
        "SELECT submitted_at, deadline FROM decision_records"
    ).fetchone()

    assert row is not None
    assert row["submitted_at"] == "1970-01-01T00:00:00+00:00"
    assert row["deadline"].startswith("9999-12-31T23:59:59.999999")
    worker.close(timeout=1)


def test_shadow_submits_original_synchronously_and_does_not_resubmit(
    tmp_path: Path,
) -> None:
    application, _, commands, worker, records = _assembled(
        tmp_path,
        mode=AgentMode.SHADOW,
        result=DecisionResult(DecisionKind.REJECT, 9000, (), (), "would reject"),
    )

    receipt = application.target_position(
        InstrumentId("BTCUSDT"), Decimal("1"), account="main"
    )
    terminal = _wait_any_terminal(records)

    assert receipt.status is SubmissionStatus.ACCEPTED
    assert receipt.delivery_certainty is DeliveryCertainty.SENT
    assert terminal.status is DecisionStatus.REJECTED
    assert terminal.final_submission_status == "accepted"
    assert len(commands.calls) == 1
    worker.close(timeout=1)


def test_revise_records_original_and_effective_in_execution_admission(
    tmp_path: Path,
) -> None:
    application, _, commands, worker, records = _assembled(
        tmp_path,
        mode=AgentMode.REVISE,
        result=DecisionResult(
            DecisionKind.REVISE,
            9000,
            (),
            (),
            "reduce target",
            (ReduceTargetQuantity("1"),),
        ),
    )

    receipt = application.target_position(
        InstrumentId("BTCUSDT"), Decimal("2"), account="main"
    )
    terminal = _wait_any_terminal(records)

    assert receipt.status is SubmissionStatus.PENDING
    assert terminal.status is DecisionStatus.REVISED
    assert len(commands.calls) == 1
    admission = commands.calls[0][1]["admission_evidence"]
    assert getattr(admission, "outcome") == "revised"
    assert getattr(admission, "original_intent").quantity == Decimal("2")
    assert getattr(admission, "effective_intent").quantity == Decimal("1")
    worker.close(timeout=1)


def test_required_context_failure_fails_closed_but_reduce_bypasses(
    tmp_path: Path,
) -> None:
    application, agent, commands, worker, records = _assembled(
        tmp_path / "closed",
        mode=AgentMode.GATE,
        result=DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved"),
        required_contexts=("signal",),
    )
    rejected = application.target_position(
        InstrumentId("BTCUSDT"), Decimal("1"), account="main"
    )
    assert rejected.status is SubmissionStatus.REJECTED
    assert rejected.delivery_certainty is DeliveryCertainty.NOT_SENT
    assert commands.calls == []
    failed = _wait_any_terminal(records)
    assert failed.status is DecisionStatus.FAILED
    assert failed.delivery_certainty == "not_sent"

    agent.publish_context(
        AgentContextDocument(
            "signal",
            ("execution.intent_review",),
            {"edge_bps": 20},
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        )
    )
    pending = application.target_position(
        InstrumentId("BTCUSDT"), Decimal("1"), account="main"
    )
    assert pending.status is SubmissionStatus.PENDING
    worker.close(timeout=1)

    reducing, _, reducing_commands, reducing_worker, reducing_records = _assembled(
        tmp_path / "reduce",
        mode=AgentMode.GATE,
        result=DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved"),
        required_contexts=("signal",),
        exposure="reduce",
    )
    bypassed = reducing.target_position(
        InstrumentId("BTCUSDT"), Decimal("0"), account="main"
    )
    assert bypassed.status is SubmissionStatus.ACCEPTED
    assert len(reducing_commands.calls) == 1
    assert reducing_commands.calls[0][1]["admission_evidence"] is None
    reduced = _wait_any_terminal(reducing_records)
    assert reduced.status is DecisionStatus.ABSTAINED
    assert reduced.delivery_certainty == "sent"
    reducing_worker.close(timeout=1)


def test_shadow_required_context_failure_submits_original_once_and_records_failure(
    tmp_path: Path,
) -> None:
    application, _, commands, worker, records = _assembled(
        tmp_path,
        mode=AgentMode.SHADOW,
        result=DecisionResult(DecisionKind.APPROVE, 9000, (), (), "unused"),
        required_contexts=("signal",),
    )

    submitted = application.target_position(
        InstrumentId("BTCUSDT"), Decimal("1"), account="main"
    )

    assert submitted.status is SubmissionStatus.ACCEPTED
    assert len(commands.calls) == 1
    assert commands.calls[0][1]["admission_evidence"] is None
    terminal = _wait_any_terminal(records)
    assert terminal.status is DecisionStatus.FAILED
    assert terminal.delivery_certainty == "sent"
    assert "missing or expired" in (terminal.reason or "")
    assert worker.health()["rolling_error_rate"] == 1.0
    worker.close(timeout=1)


def test_runtime_failure_reduction_bypasses_without_agent_admission_evidence(
    tmp_path: Path,
) -> None:
    application, _, commands, worker, records = _assembled(
        tmp_path,
        mode=AgentMode.GATE,
        result=DecisionResult(DecisionKind.APPROVE, 9000, (), (), "unused"),
        exposure="reduce",
        runtime=FailingRuntime(),
    )

    pending = application.target_position(
        InstrumentId("BTCUSDT"), Decimal("0"), account="main"
    )
    terminal = _wait_any_terminal(records)

    assert pending.status is SubmissionStatus.PENDING
    assert terminal.status is DecisionStatus.ABSTAINED
    assert terminal.delivery_certainty == "sent"
    assert len(commands.calls) == 1
    assert commands.calls[0][1]["admission_evidence"] is None
    worker.close(timeout=1)


def test_queue_full_reduction_returns_downstream_status_without_agent_evidence(
    tmp_path: Path,
) -> None:
    runtime = BlockingRuntime(
        DecisionResult(DecisionKind.APPROVE, 9000, (), (), "approved")
    )
    application, _, commands, worker, _ = _assembled(
        tmp_path,
        mode=AgentMode.GATE,
        result=runtime.result,
        exposure="reduce",
        runtime=runtime,
        queue_capacity=1,
    )

    first = application.target_position(
        InstrumentId("BTCUSDT"), Decimal("1"), account="main"
    )
    assert first.status is SubmissionStatus.PENDING
    assert runtime.entered.wait(1)
    second = application.target_position(
        InstrumentId("BTCUSDT"), Decimal("1"), account="main"
    )
    bypassed = application.target_position(
        InstrumentId("BTCUSDT"), Decimal("0"), account="main"
    )

    assert second.status is SubmissionStatus.PENDING
    assert bypassed.status is SubmissionStatus.ACCEPTED
    assert bypassed.delivery_certainty is DeliveryCertainty.SENT
    assert len(commands.calls) == 1
    assert commands.calls[0][1]["admission_evidence"] is None
    runtime.release.set()
    assert worker.wait_idle(timeout=1)
    worker.close(timeout=1)


def test_unavailable_runtime_allows_only_proven_reduction() -> None:
    commands = Commands()
    unavailable = UnavailableAgentExecutionCommands(
        commands,
        agent=AgentApplication(enabled=True, initial_mode=AgentMode.GATE),
        operations=("target_position",),
        classify_exposure=lambda request: "reduce",
    )

    result = unavailable.target_position(
        TargetPositionRequest("BTCUSDT", Decimal("0"), account_id="main"),
        request_id="request",
        strategy_id="strategy",
        instance_id="instance",
    )

    assert result.status == "accepted"
    assert len(commands.calls) == 1
    assert "admission_evidence" not in commands.calls[0][1]


def _wait_any_terminal(records: DecisionRecordStore):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        recent = records.recent(limit=1)
        if recent and recent[0].status not in {
            DecisionStatus.PENDING,
            DecisionStatus.RUNNING,
            DecisionStatus.SUBMITTING,
        }:
            return recent[0]
        time.sleep(0.01)
    raise AssertionError("Decision did not complete")
