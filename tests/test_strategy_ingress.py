from __future__ import annotations

import asyncio

import pytest

from kairospy.strategy.apps.agent.application import (
    AgentDecisionNotice,
    AgentEvent,
    AgentEventStatus,
)
from kairospy.strategy.apps.runtime.services.ingress import StrategyEventIngress
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

    async def events(self):
        for event in self._records:
            await asyncio.sleep(0)
            yield event


class _FailedSource:
    async def events(self):
        yield _event(1)
        raise RuntimeError("source failed")


class _EmptySource:
    async def events(self):
        if False:
            yield _event(1)


class _UnexpectedSource:
    async def events(self):
        raise AssertionError("disabled source must not be started")
        yield _event(1)


def _ingress(
    market: object | None = None,
    account: object | None = None,
    *,
    queue_size: int = 256,
    agent_events=None,
) -> StrategyEventIngress:
    return StrategyEventIngress(
        market=market or _EmptySource(),  # type: ignore[arg-type]
        account=account or _EmptySource(),  # type: ignore[arg-type]
        risk=_EmptySource(),  # type: ignore[arg-type]
        execution=_EmptySource(),  # type: ignore[arg-type]
        agent_events=agent_events,
        queue_size=queue_size,
    )


def test_ingress_merges_multiple_typed_sources_without_dropping_events() -> None:
    async def run() -> None:
        ingress = _ingress(
            _FiniteSource([_event(1, "system:a"), _event(2, "system:a")]),
            _FiniteSource([_event(1, "system:b"), _event(2, "system:b")]),
            queue_size=1,
        )

        received = [dispatch async for dispatch in ingress.events()]

        assert len(received) == 4
        assert {
            (dispatch.event.metadata.stream_id, dispatch.event.metadata.sequence)
            for dispatch in received
        } == {("system:a", 1), ("system:a", 2), ("system:b", 1), ("system:b", 2)}
        assert all(dispatch.hook == "on_system" for dispatch in received)

    asyncio.run(run())


def test_ingress_propagates_source_failure_and_cancels_other_pumps() -> None:
    async def run() -> None:
        ingress = _ingress(
            _FailedSource(),
            _FiniteSource([_event(1, "system:healthy")]),
            queue_size=1,
        )
        stream = ingress.events()

        await anext(stream)
        with pytest.raises(RuntimeError, match="source failed"):
            while True:
                await anext(stream)

    asyncio.run(run())


def test_ingress_rejects_an_unbounded_zero_capacity_configuration() -> None:
    with pytest.raises(ValueError, match="positive"):
        _ingress(queue_size=0)


def test_ingress_does_not_start_market_without_strategy_demand() -> None:
    async def run() -> None:
        ingress = _ingress(_UnexpectedSource())
        assert [item async for item in ingress.events(include_market=False)] == []

    asyncio.run(run())


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
