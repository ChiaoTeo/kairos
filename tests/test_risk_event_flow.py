from __future__ import annotations

from types import SimpleNamespace

import pytest

from kairospy.contracts.risk.events import RiskEvent
from kairospy.investment.apps.risk.application import RiskApplication
from kairospy.primitives.account import AccountId


class FiniteRiskSource:
    def __init__(self, records: list[object]) -> None:
        self.records = records

    def poll_visit(self, visitor, *, fragment_limit: int = 64) -> int:
        records, self.records = self.records[:fragment_limit], self.records[fragment_limit:]
        for record in records:
            visitor(record)
        return len(records)

    def close(self) -> None:
        return None


class LiveRiskSource(FiniteRiskSource):
    join_from_latest = True


def record(sequence: int, *, account: str, strategy: str, **scope: str) -> object:
    return RiskEvent.reservation_changed(
        sequence, account, strategy, **scope
    )


def collect(application: RiskApplication) -> list[object]:
    events: list[object] = []
    application.visit_live(events.append)
    return events


def test_risk_application_filters_owner_native_business_events() -> None:
    application = RiskApplication(
        None,
        FiniteRiskSource(
            [
                record(1, account="outside", strategy="strategy-1"),
                record(2, account="main", strategy="outside"),
                record(3, account="main", strategy="strategy-1"),
            ]
        ),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )

    events = collect(application)
    assert len(events) == 1
    assert isinstance(events[0], RiskEvent)
    assert events[0].account_id == "main"
    assert events[0].metadata.sequence == 3


def test_risk_application_uses_first_live_event_as_attach_baseline() -> None:
    application = RiskApplication(
        None,
        FiniteRiskSource([record(2, account="main", strategy="strategy-1")]),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    assert [event.metadata.sequence for event in collect(application)] == [2]


def test_risk_application_preserves_native_decision_and_circuit_payloads() -> None:
    decision = RiskEvent.decision_evaluated(
        1,
        "main",
        "strategy-1",
        "decision-1",
        "request-1",
        False,
        degraded=True,
        reason_codes=["limit_exceeded"],
        violations=["notional limit"],
    )
    circuit = RiskEvent.circuit_changed(
        2, "open", reason="loss limit", opened_at_unix_nanos=2
    )
    application = RiskApplication(
        None,
        FiniteRiskSource([decision, circuit]),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )

    events = collect(application)
    assert events[0] is decision
    assert events[0].data.reason_codes == ["limit_exceeded"]
    assert events[1] is circuit
    assert events[1].data.open is True


def test_classified_risk_events_advance_sequence_and_reach_the_callback() -> None:
    circuit = RiskEvent.circuit_changed(1, "closed")
    application = RiskApplication(
        None,
        FiniteRiskSource([circuit, record(2, account="main", strategy="strategy-1")]),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    events = collect(application)
    assert len(events) == 2
    assert [event.metadata.sequence for event in events] == [1, 2]


def test_risk_ignores_duplicates_and_rejects_non_native_events() -> None:
    application = RiskApplication(
        None,
        FiniteRiskSource(
            [
                record(1, account="main", strategy="strategy-1"),
                record(2, account="main", strategy="strategy-1"),
                record(1, account="main", strategy="strategy-1"),
                record(3, account="main", strategy="strategy-1"),
            ]
        ),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    assert len(collect(application)) == 3

    invalid = RiskApplication(
        None,
        FiniteRiskSource([SimpleNamespace(stream_id="risk.events", sequence=1)]),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    with pytest.raises(AttributeError, match="metadata"):
        collect(invalid)


def test_live_risk_source_joins_latest_then_reports_gap() -> None:
    application = RiskApplication(
        None,
        LiveRiskSource(
            [record(40, account="main", strategy="strategy-1"), record(41, account="main", strategy="strategy-1")]
        ),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    assert len(collect(application)) == 2

    gap = RiskApplication(
        None,
        LiveRiskSource(
            [record(40, account="main", strategy="strategy-1"), record(42, account="main", strategy="strategy-1")]
        ),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    assert len(collect(gap)) == 2
    assert gap.notification_health()["gap_count"] == 1


def test_risk_rejects_another_launch_instance_before_business_scope() -> None:
    application = RiskApplication(
        None,
        FiniteRiskSource(
            [record(1, account="main", strategy="strategy-1", launch_id="launch", instance_id="other")]
        ),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
        launch_id="launch",
        instance_id="instance",
    )
    with pytest.raises(RuntimeError, match="another launch instance"):
        collect(application)
