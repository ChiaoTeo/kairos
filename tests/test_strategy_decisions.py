from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from kairospy.investment.apps.execution.application import (
    ExecutionIntent,
    Fill,
    FillEvent,
    IntentStatus,
    IntentUpdateEvent,
)
from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.strategy.apps.decisions.application import (
    DecisionHorizon,
    DecisionLifecycle,
    EffectEvidence,
    StrategyDecisionApplication,
)
from kairospy.strategy.apps.decisions.services.journal import (
    StrategyDecisionJournal,
)
from kairospy.investment.application.eventing import EventMetadata
from kairospy.primitives.account import AccountId
from kairospy.primitives.execution import FillId, IntentId, OrderId
from kairospy.primitives.reference import InstrumentId
from kairospy.strategy.api.clock import DeterministicTimerQueue, StrategyClock


class RecordingNotifications:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def publish(self, **request: object) -> None:
        self.requests.append(dict(request))


def _application(
    path: Path, notifications: RecordingNotifications | None = None
) -> tuple[StrategyDecisionApplication, DeterministicTimerQueue, StrategyClock]:
    timers = DeterministicTimerQueue()
    clock = StrategyClock(timers.schedule, timers.cancel)
    clock._set_now(datetime(2026, 8, 18, tzinfo=timezone.utc))
    return (
        StrategyDecisionApplication(
            strategy_id="strategy-a",
            instance_id="instance-1",
            journal=StrategyDecisionJournal(path),
            notifications=notifications or RecordingNotifications(),  # type: ignore[arg-type]
            clock=clock,
            notification_routes=("intent-lifecycle",),
        ),
        timers,
        clock,
    )


def _intent_event(
    decision_id: str | None,
    intent_id: str,
    status: IntentStatus,
    sequence: int,
    occurred_at: datetime,
) -> IntentUpdateEvent:
    intent = ExecutionIntent(
        id=IntentId(intent_id),
        strategy_id="strategy-a",
        instrument=InstrumentRef(InstrumentId("instrument:test:BTCUSDT"), "BTCUSDT"),
        account_ids=(AccountId("main"),),
        target_quantity=None,
        status=status,
        reason=status.value,
        order_ids=(),
        strategy_decision_id=decision_id,
    )
    return IntentUpdateEvent(
        intent,
        EventMetadata(
            "execution.events",
            sequence,
            producer="execution",
            occurred_at=occurred_at,
            occurred_at_unix_nanos=int(occurred_at.timestamp() * 1_000_000_000),
        ),
    )


def test_decision_progress_waits_for_every_expected_intent(tmp_path: Path) -> None:
    notifications = RecordingNotifications()
    decisions, _, clock = _application(tmp_path / "decisions.jsonl", notifications)
    decision = decisions.record(
        strategy_decision_id="strategy-a:decision:one",
        reason="rebalance",
        expected_outcome="reduce tracking error",
        expected_intent_count=2,
    )
    now = clock.now
    assert now is not None

    decisions.observe_execution(
        _intent_event(
            decision.strategy_decision_id, "intent-1", IntentStatus.SATISFIED, 1, now
        )
    )
    assert (
        decisions.decision(decision.strategy_decision_id).lifecycle
        is DecisionLifecycle.SUBMITTED
    )  # type: ignore[union-attr]

    decisions.observe_execution(
        _intent_event(
            decision.strategy_decision_id, "intent-2", IntentStatus.FAILED, 2, now
        )
    )
    decisions.observe_execution(
        _intent_event(
            decision.strategy_decision_id, "intent-2", IntentStatus.FAILED, 2, now
        )
    )
    completed = decisions.decision(decision.strategy_decision_id)
    assert completed is not None
    assert completed.lifecycle is DecisionLifecycle.EVALUATION_PENDING
    assert completed.due_horizons == ("execution-final",)
    assert completed.intent_ids == ("intent-1", "intent-2")
    assert [request["dedupe_key"] for request in notifications.requests] == [
        "decision:strategy-a:decision:one:recorded",
        "intent:intent-1:lifecycle:1",
        "intent:intent-2:lifecycle:2",
        "decision:strategy-a:decision:one:execution-completed",
    ]


@pytest.mark.parametrize(
    "status",
    [
        IntentStatus.SATISFIED,
        IntentStatus.COMPLETED,
        IntentStatus.REJECTED,
        IntentStatus.CANCELED,
        IntentStatus.EXPIRED,
        IntentStatus.FAILED,
        IntentStatus.RECONCILIATION_REQUIRED,
    ],
)
def test_each_execution_terminal_advances_decision_progress(
    tmp_path: Path, status: IntentStatus
) -> None:
    decisions, _, clock = _application(tmp_path / f"{status.value}.jsonl")
    decision = decisions.record(
        strategy_decision_id=f"decision-{status.value}",
        reason="test terminal",
        expected_outcome="terminal is observed",
    )
    assert clock.now is not None
    state = decisions.observe_execution(
        _intent_event(decision.strategy_decision_id, "intent-1", status, 1, clock.now)
    )
    assert state is not None
    assert state.lifecycle is DecisionLifecycle.EVALUATION_PENDING


def test_zero_intent_decision_is_explicitly_not_submitted(tmp_path: Path) -> None:
    decisions, _, _ = _application(tmp_path / "no-intent.jsonl")
    decision = decisions.record(
        strategy_decision_id="decision-no-intent",
        reason="threshold not met",
        expected_outcome="no trade",
        expected_intent_count=0,
    )
    assert decision.lifecycle is DecisionLifecycle.NOT_SUBMITTED
    assert decision.intent_ids == ()


def test_decision_progress_rejects_a_lifecycle_gap(tmp_path: Path) -> None:
    decisions, _, clock = _application(tmp_path / "gap.jsonl")
    decision = decisions.record(
        strategy_decision_id="decision-gap",
        reason="test lifecycle continuity",
        expected_outcome="gap is detected",
    )
    assert clock.now is not None
    accepted = _intent_event(
        decision.strategy_decision_id,
        "intent-1",
        IntentStatus.ACCEPTED,
        1,
        clock.now,
    )
    decisions.observe_execution(accepted)
    executing = _intent_event(
        decision.strategy_decision_id,
        "intent-1",
        IntentStatus.EXECUTING,
        2,
        clock.now,
    )
    executing = IntentUpdateEvent(
        executing.data, executing.metadata, IntentStatus.PLANNED
    )
    with pytest.raises(ValueError, match="not contiguous"):
        decisions.observe_execution(executing)
    assert decisions.health()["lifecycle_sequence_gap_count"] == 1


def test_snapshot_recovery_advances_without_personnel_notification(
    tmp_path: Path,
) -> None:
    notifications = RecordingNotifications()
    decisions, _, _ = _application(tmp_path / "recovery.jsonl", notifications)
    decision = decisions.record(
        strategy_decision_id="decision-recovery",
        reason="recover execution progress",
        expected_outcome="terminal state is restored",
    )
    decisions.attach_intent(decision.strategy_decision_id, "intent-recovery")
    notifications.requests.clear()
    recovered = ExecutionIntent(
        id=IntentId("intent-recovery"),
        strategy_id="strategy-a",
        instrument=InstrumentRef(InstrumentId("instrument:test:BTCUSDT"), "BTCUSDT"),
        account_ids=(AccountId("main"),),
        target_quantity=Decimal("1"),
        status=IntentStatus.SATISFIED,
        reason="recovered from current view",
        order_ids=(OrderId("order-1"),),
        strategy_decision_id=decision.strategy_decision_id,
        updated_at_unix_nanos=10,
    )

    state = decisions.reconcile_execution_snapshot(recovered, source_event_sequence=7)

    assert state is not None
    assert state.lifecycle is DecisionLifecycle.EVALUATION_PENDING
    assert notifications.requests == []


def test_effect_evaluation_is_revisioned_and_restored(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    decisions, _, clock = _application(path)
    decision = decisions.record(
        strategy_decision_id="decision-effect",
        reason="enter on spread dislocation",
        expected_outcome="positive one-minute markout",
        evidence=(
            EffectEvidence(
                owner="market",
                reference_id="quote:decision",
                event_sequence=8,
            ),
        ),
    )
    now = clock.now
    assert now is not None
    decisions.observe_execution(
        _intent_event(
            decision.strategy_decision_id, "intent-1", IntentStatus.SATISFIED, 1, now
        )
    )
    evidence = (
        EffectEvidence(
            owner="market",
            reference_id="quote:btc:1m",
            event_sequence=9,
            observed_at_unix_nanos=int(now.timestamp() * 1_000_000_000),
            value="12",
            unit="bps",
        ),
    )
    first = decisions.evaluate(
        decision.strategy_decision_id,
        horizon="execution-final",
        outcome="favorable",
        summary="filled inside the decision price",
        evidence=evidence,
    )
    corrected = decisions.evaluate(
        decision.strategy_decision_id,
        horizon="execution-final",
        outcome="neutral",
        summary="late fee correction reduced the effect",
        evidence=evidence,
    )
    assert first.revision == 1
    assert corrected.revision == 2

    replay_notifications = RecordingNotifications()
    restored, _, _ = _application(path, replay_notifications)
    state = restored.decision(decision.strategy_decision_id)
    assert state is not None
    assert state.lifecycle is DecisionLifecycle.EVALUATED
    assert state.evidence[0].reference_id == "quote:decision"
    assert [value.revision for value in state.evaluations] == [1, 2]
    assert state.due_horizons == ()
    assert replay_notifications.requests == []


def test_timed_horizon_recovers_and_becomes_due_on_business_clock(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decisions.jsonl"
    decisions, _, clock = _application(path)
    decision = decisions.record(
        strategy_decision_id="decision-timed",
        reason="enter momentum",
        expected_outcome="positive markout",
        horizons=(DecisionHorizon.after("markout-1m", "1m"),),
    )
    now = clock.now
    assert now is not None
    decisions.observe_execution(
        _intent_event(
            decision.strategy_decision_id, "intent-1", IntentStatus.SATISFIED, 1, now
        )
    )

    restored, timers, restored_clock = _application(path)
    due_at = now + timedelta(minutes=1)
    restored_clock._set_now(due_at)
    due = timers.pop_due(due_at)
    assert len(due) == 1
    assert restored.observe_timer(due[0]) == "markout-1m"
    assert restored.pending_evaluations() == (("decision-timed", "markout-1m"),)

    restored.evaluate(
        decision.strategy_decision_id,
        horizon="markout-1m",
        outcome="unavailable",
        summary="test fixture has no later market observation",
        evidence=(),
        evaluated_at=due_at,
    )
    completed, completed_timers, completed_clock = _application(path)
    completed_clock._set_now(due_at + timedelta(minutes=1))
    assert completed_clock.now is not None
    assert completed_timers.pop_due(completed_clock.now) == []
    assert completed.pending_evaluations() == ()


def test_unattributed_legacy_intent_is_counted_without_fabricated_decision(
    tmp_path: Path,
) -> None:
    decisions, _, clock = _application(tmp_path / "decisions.jsonl")
    now = clock.now
    assert now is not None
    assert (
        decisions.observe_execution(
            _intent_event(None, "legacy-intent", IntentStatus.SATISFIED, 1, now)
        )
        is None
    )
    assert decisions.health()["unattributed_intent_count"] == 1


def test_late_fill_requires_a_new_effect_revision(tmp_path: Path) -> None:
    decisions, _, clock = _application(tmp_path / "decisions.jsonl")
    now = clock.now
    assert now is not None
    decision = decisions.record(
        strategy_decision_id="decision-late-fill",
        reason="enter position",
        expected_outcome="positive markout",
    )
    decisions.observe_execution(
        _intent_event(
            decision.strategy_decision_id, "intent-1", IntentStatus.SATISFIED, 1, now
        )
    )
    evidence = (
        EffectEvidence(owner="market", reference_id="quote:1", event_sequence=3),
    )
    decisions.evaluate(
        decision.strategy_decision_id,
        horizon="execution-final",
        outcome="favorable",
        summary="initial evaluation",
        evidence=evidence,
    )

    decisions.observe_execution(
        FillEvent(
            Fill(
                id=FillId("fill-late"),
                order_id=OrderId("order-1"),
                instrument=InstrumentRef(
                    InstrumentId("instrument:test:BTCUSDT"), "BTCUSDT"
                ),
                quantity=Decimal("1"),
                price=Decimal("100"),
                occurred_at=now,
                intent_id=IntentId("intent-1"),
            ),
            EventMetadata(
                "execution.events",
                2,
                producer="execution",
                occurred_at=now,
                occurred_at_unix_nanos=int(now.timestamp() * 1_000_000_000),
            ),
        )
    )

    state = decisions.decision(decision.strategy_decision_id)
    assert state is not None
    assert state.lifecycle is DecisionLifecycle.EVALUATION_PENDING
    assert state.due_horizons == ("execution-final",)
    revision = decisions.evaluate(
        decision.strategy_decision_id,
        horizon="execution-final",
        outcome="neutral",
        summary="late fill incorporated",
        evidence=evidence,
    )
    assert revision.revision == 2


def test_snapshot_recovery_of_late_fill_requires_a_new_effect_revision(
    tmp_path: Path,
) -> None:
    decisions, _, clock = _application(tmp_path / "snapshot-late-fill.jsonl")
    now = clock.now
    assert now is not None
    decision = decisions.record(
        strategy_decision_id="decision-snapshot-late-fill",
        reason="enter position",
        expected_outcome="positive markout",
    )
    decisions.observe_execution(
        _intent_event(
            decision.strategy_decision_id, "intent-1", IntentStatus.SATISFIED, 1, now
        )
    )
    decisions.evaluate(
        decision.strategy_decision_id,
        horizon="execution-final",
        outcome="favorable",
        summary="initial evaluation",
        evidence=(
            EffectEvidence(owner="market", reference_id="quote:1", event_sequence=3),
        ),
    )

    decisions.reconcile_execution_fill(
        Fill(
            id=FillId("fill-recovered"),
            order_id=OrderId("order-1"),
            instrument=InstrumentRef(
                InstrumentId("instrument:test:BTCUSDT"), "BTCUSDT"
            ),
            quantity=Decimal("1"),
            price=Decimal("100"),
            occurred_at=now,
            intent_id=IntentId("intent-1"),
        ),
        source_event_sequence=7,
    )

    recovered = decisions.decision(decision.strategy_decision_id)
    assert recovered is not None
    assert recovered.lifecycle is DecisionLifecycle.EVALUATION_PENDING
    assert recovered.due_horizons == ("execution-final",)


def test_decision_values_are_available_from_public_strategy_api() -> None:
    from kairospy.strategy import DecisionHorizon as PublicDecisionHorizon
    from kairospy.strategy import EffectEvidence as PublicEffectEvidence

    assert PublicDecisionHorizon is DecisionHorizon
    assert PublicEffectEvidence is EffectEvidence
