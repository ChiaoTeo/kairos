from __future__ import annotations

import asyncio

import pytest

from kairospy.strategy.apps.agent.application import (
    AgentDecisionNotice,
    AgentEvent,
    AgentEventStatus,
)
from kairospy.strategy.apps.runtime.services.ingress import (
    StrategyEventIngress,
    StrategySourceError,
)
from kairospy.investment.application.eventing import EventMetadata
from kairospy.strategy import SystemEvent, SystemNotice


def _event(sequence: int, stream_id: str = "system:test") -> SystemEvent:
    return SystemEvent(
        SystemNotice("test", str(sequence)),
        EventMetadata(stream_id=stream_id, sequence=sequence, producer="test"),
    )


class _FiniteSource:
    def __init__(self, events: list[SystemEvent]) -> None:
        self._records = events

    def visit_live(self, visitor, *, fragment_limit: int = 64) -> int:
        records, self._records = (
            self._records[:fragment_limit],
            self._records[fragment_limit:],
        )
        for event in records:
            visitor(event)
        return len(records)

    def close_live(self) -> None:
        return None


class _FailedSource:
    def visit_live(self, visitor, *, fragment_limit: int = 64) -> int:
        raise RuntimeError("source failed")

    def close_live(self) -> None:
        return None


class _EmptySource:
    def visit_live(self, visitor, *, fragment_limit: int = 64) -> int:
        return 0

    def close_live(self) -> None:
        return None


class _UnexpectedSource:
    def visit_live(self, visitor, *, fragment_limit: int = 64) -> int:
        raise AssertionError("disabled source must not be started")

    def close_live(self) -> None:
        return None


def _ingress(
    market: _FiniteSource | _FailedSource | _UnexpectedSource | None = None,
    account: _FiniteSource | None = None,
    *,
    queue_size: int = 256,
    agent_events=None,
) -> StrategyEventIngress:
    return StrategyEventIngress(
        market=market or _EmptySource(),
        account=account or _EmptySource(),
        risk=_EmptySource(),
        execution=_EmptySource(),
        agent_events=agent_events,
        queue_size=queue_size,
    )


def test_ingress_merges_multiple_typed_sources_without_dropping_events() -> None:
    ingress = _ingress(
        _FiniteSource([_event(1, "system:a"), _event(2, "system:a")]),
        _FiniteSource([_event(1, "system:b"), _event(2, "system:b")]),
        queue_size=1,
    )
    received = []

    summary = ingress.poll_once(received.append)

    assert summary.fragment_count == 4
    assert len(received) == 4
    assert [dispatch.domain for dispatch in received] == [
        "market",
        "market",
        "account",
        "account",
    ]


def test_ingress_propagates_source_failure_and_cancels_other_pumps() -> None:
    ingress = _ingress(
        _FailedSource(),
        _FiniteSource([_event(1, "system:healthy")]),
        queue_size=1,
    )

    with pytest.raises(StrategySourceError, match="source failed"):
        ingress.poll_once(lambda _dispatch: None)


def test_ingress_rejects_an_unbounded_zero_capacity_configuration() -> None:
    with pytest.raises(ValueError, match="positive"):
        _ingress(queue_size=0)


def test_ingress_does_not_start_market_without_strategy_demand() -> None:
    ingress = _ingress(_UnexpectedSource())
    summary = ingress.poll_once(lambda _dispatch: None, include_market=False)
    assert summary.fragment_count == 0


def test_ingress_routes_agent_notice_to_on_agent() -> None:
    event = AgentEvent(
        AgentDecisionNotice(
            "decision-1",
            "execution.intent_review",
            AgentEventStatus.REJECTED,
            ("risk_too_high",),
        ),
        EventMetadata("agent.decisions:instance", 1, producer="strategy.agent"),
    )

    dispatch = StrategyEventIngress.route(event)

    assert dispatch.domain == "agent"
    assert dispatch.hook == "on_agent"
    assert dispatch.event is event
