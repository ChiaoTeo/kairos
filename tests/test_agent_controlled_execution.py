from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
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
)
from kairospy.application.execution import (
    DeliveryCertainty,
    ExecutionApplication,
    SubmissionStatus,
)
from kairospy.domain_types import InstrumentId
from kairospy.strategy import CommandResult


class Runtime:
    def __init__(self, result: DecisionResult) -> None:
        self.result = result

    def decide(self, candidate):
        return self.result


class Commands:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, str]]] = []

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
    admission_evidence: list[object] | None = None,
):
    agent = AgentApplication(enabled=True, initial_mode=mode)
    records = DecisionRecordStore(tmp_path / "decisions.sqlite3")
    worker = AgentDecisionWorker(
        runtime=Runtime(result),
        policy=DecisionPolicy({"allow_quantity_reduction": True}),
        records=records,
        queue_capacity=8,
    )
    worker.start()
    commands = Commands()
    controlled = AgentControlledExecutionCommands(
        commands,
        agent=agent,
        worker=worker,
        launch_id="launch",
        profile_hash="profile-hash",
        operations=("target_position",),
        required_contexts=required_contexts,
        max_decision_age_seconds=5,
        classify_exposure=lambda request: exposure,
        record_admission=(
            None if admission_evidence is None else admission_evidence.append
        ),
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
    evidence: list[object] = []
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
        admission_evidence=evidence,
    )

    receipt = application.target_position(
        InstrumentId("BTCUSDT"), Decimal("2"), account="main"
    )
    terminal = _wait_any_terminal(records)

    assert receipt.status is SubmissionStatus.PENDING
    assert terminal.status is DecisionStatus.REVISED
    assert len(commands.calls) == 1
    assert len(evidence) == 1
    admission = evidence[0]
    assert getattr(admission, "outcome") == "revised"
    assert getattr(admission, "original_intent").quantity == Decimal("2")
    assert getattr(admission, "effective_intent").quantity == Decimal("1")
    worker.close(timeout=1)


def test_required_context_failure_fails_closed_but_reduce_bypasses(
    tmp_path: Path,
) -> None:
    application, agent, commands, worker, _ = _assembled(
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

    reducing, _, reducing_commands, reducing_worker, _ = _assembled(
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
    reducing_worker.close(timeout=1)


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
