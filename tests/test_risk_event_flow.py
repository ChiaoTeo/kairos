from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kairospy.infrastructure.contracts.risk.events import RiskEvent
from kairospy.investment.apps.risk.application import RiskApplication
from kairospy.primitives.account import AccountId


class FiniteRiskSource:
    def __init__(self, records: list[object]) -> None:
        self.records = records

    async def subscribe_live(self):
        for record in self.records:
            yield record


class LiveRiskSource(FiniteRiskSource):
    join_from_latest = True


def record(sequence: int, *, account: str, strategy: str, **scope: str) -> object:
    return RiskEvent.reservation_changed(
        sequence, account, strategy, **scope
    )


async def collect(application: RiskApplication) -> list[object]:
    return [event async for event in application.events()]


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

    events = asyncio.run(collect(application))
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
    assert [event.metadata.sequence for event in asyncio.run(collect(application))] == [2]


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

    events = asyncio.run(collect(application))
    assert events[0] is decision
    assert events[0].data.reason_codes == ["limit_exceeded"]
    assert events[1] is circuit
    assert events[1].data.open is True


def test_internal_policy_event_advances_sequence_without_strategy_callback() -> None:
    policy = RiskEvent.policy_activated(1)
    application = RiskApplication(
        None,
        FiniteRiskSource([policy, record(2, account="main", strategy="strategy-1")]),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    events = asyncio.run(collect(application))
    assert len(events) == 1
    assert events[0].metadata.sequence == 2


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
    assert len(asyncio.run(collect(application))) == 3

    invalid = RiskApplication(
        None,
        FiniteRiskSource([SimpleNamespace(stream_id="risk.events", sequence=1)]),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    with pytest.raises(TypeError, match="owner-native"):
        asyncio.run(collect(invalid))


def test_live_risk_source_joins_latest_then_enforces_continuity() -> None:
    application = RiskApplication(
        None,
        LiveRiskSource(
            [record(40, account="main", strategy="strategy-1"), record(41, account="main", strategy="strategy-1")]
        ),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    assert len(asyncio.run(collect(application))) == 2

    gap = RiskApplication(
        None,
        LiveRiskSource(
            [record(40, account="main", strategy="strategy-1"), record(42, account="main", strategy="strategy-1")]
        ),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    with pytest.raises(RuntimeError, match="expected 41, received 42"):
        asyncio.run(collect(gap))


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
        asyncio.run(collect(application))
