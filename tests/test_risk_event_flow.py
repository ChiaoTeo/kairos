from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from kairospy.application.risk import (
    ReservationChangedEvent,
    RiskApplication,
    RiskCircuitChangedEvent,
    RiskDecisionEvent,
)
from kairospy.domain_types import AccountId
from kairospy.infrastructure.transport.risk import RiskEventRecord


class FiniteRiskSource:
    def __init__(self, records: list[RiskEventRecord]) -> None:
        self.records = records

    async def subscribe_live(self):
        for record in self.records:
            yield record


class LiveRiskSource(FiniteRiskSource):
    join_from_latest = True


def record(sequence: int, *, account: str, strategy: str) -> RiskEventRecord:
    return RiskEventRecord(
        stream_id="risk.events",
        sequence=sequence,
        producer="risk:instance-1",
        kind="reservation_changed",
        account_id=account,
        strategy_id=strategy,
        payload={
            "reservation_id": f"reservation-{sequence}",
            "request_id": f"request-{sequence}",
            "status": "reserved",
            "occurred_at_unix_nanos": sequence,
        },
        occurred_at_unix_nanos=sequence,
    )


def test_risk_application_maps_and_filters_aeron_business_events() -> None:
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

    async def collect():
        return [event async for event in application.events()]

    events = asyncio.run(collect())
    assert len(events) == 1
    assert isinstance(events[0], ReservationChangedEvent)
    assert events[0].data.account_id == AccountId("main")
    assert events[0].metadata.sequence == 3


def test_risk_application_uses_first_live_event_as_attach_baseline() -> None:
    application = RiskApplication(
        None,
        FiniteRiskSource([record(2, account="main", strategy="strategy-1")]),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )

    async def collect():
        return [event async for event in application.events()]

    events = asyncio.run(collect())

    assert [event.metadata.sequence for event in events] == [2]


def test_risk_application_maps_decisions_and_global_circuit_changes() -> None:
    decision = RiskEventRecord(
        "risk.events",
        1,
        "risk:instance-1",
        "decision_evaluated",
        "main",
        "strategy-1",
        {
            "decision_id": "decision-1",
            "request_id": "request-1",
            "allowed": False,
            "degraded": True,
            "reason_codes": ("limit_exceeded",),
            "violations": ("notional limit",),
        },
        1,
    )
    circuit = RiskEventRecord(
        "risk.events",
        2,
        "risk:instance-1",
        "circuit_changed",
        None,
        None,
        {
            "exchange_id": None,
            "state": "open",
            "reason": "loss limit",
            "opened_at_unix_nanos": 2,
            "reset_at_unix_nanos": 0,
        },
        2,
    )
    application = RiskApplication(
        None,
        FiniteRiskSource([decision, circuit]),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )

    async def collect():
        return [event async for event in application.events()]

    events = asyncio.run(collect())
    assert isinstance(events[0], RiskDecisionEvent)
    assert events[0].data.reason_codes == ("limit_exceeded",)
    assert isinstance(events[1], RiskCircuitChangedEvent)
    assert events[1].data.open is True


def test_internal_policy_event_advances_sequence_without_strategy_callback() -> None:
    policy = RiskEventRecord(
        "risk.events", 1, "risk:instance-1", "policy_activated", None, None, {}, 1
    )
    application = RiskApplication(
        None,
        FiniteRiskSource([policy, record(2, account="main", strategy="strategy-1")]),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )

    async def collect():
        return [event async for event in application.events()]

    events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0].metadata.sequence == 2


def test_risk_ignores_duplicates_and_rejects_wrong_stream_identity() -> None:
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

    async def collect(source):
        return [event async for event in source.events()]

    assert len(asyncio.run(collect(application))) == 3
    invalid = record(1, account="main", strategy="strategy-1")
    invalid = RiskEventRecord(
        "other.events",
        invalid.sequence,
        invalid.producer,
        invalid.kind,
        invalid.account_id,
        invalid.strategy_id,
        invalid.payload,
        invalid.occurred_at_unix_nanos,
    )
    application = RiskApplication(
        None,
        FiniteRiskSource([invalid]),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    with pytest.raises(RuntimeError, match="stream identity"):
        asyncio.run(collect(application))


def test_live_risk_source_joins_latest_then_enforces_continuity() -> None:
    application = RiskApplication(
        None,
        LiveRiskSource(
            [
                record(40, account="main", strategy="strategy-1"),
                record(41, account="main", strategy="strategy-1"),
            ]
        ),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )

    async def collect(source):
        return [item async for item in source.events()]

    assert len(asyncio.run(collect(application))) == 2
    application = RiskApplication(
        None,
        LiveRiskSource(
            [
                record(40, account="main", strategy="strategy-1"),
                record(42, account="main", strategy="strategy-1"),
            ]
        ),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
    )
    with pytest.raises(RuntimeError, match="expected 41, received 42"):
        asyncio.run(collect(application))


def test_risk_rejects_another_launch_instance_before_business_scope() -> None:
    application = RiskApplication(
        None,
        FiniteRiskSource(
            [
                replace(
                    record(1, account="main", strategy="strategy-1"),
                    launch_id="launch",
                    instance_id="other",
                )
            ]
        ),
        account_ids=(AccountId("main"),),
        strategy_id="strategy-1",
        launch_id="launch",
        instance_id="instance",
    )

    async def collect():
        return [item async for item in application.events()]

    with pytest.raises(RuntimeError, match="another launch instance"):
        asyncio.run(collect())
