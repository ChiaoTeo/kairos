from __future__ import annotations

import asyncio

from kairospy._native_account_contract import AccountEvent
from kairospy._native_execution_contract import ExecutionEvent
from kairospy._native_market_contract import MarketEvent
from kairospy._native_risk_contract import RiskEvent
from kairospy.investment.apps.account.application.application import AccountApplication
from kairospy.investment.apps.execution.application.application import (
    ExecutionApplication,
)
from kairospy.investment.apps.market.application.application import MarketApplication
from kairospy.investment.apps.risk.application.application import RiskApplication
from kairospy.primitives.account import AccountId


class _Events:
    def __init__(self, *records: object) -> None:
        self.records = records

    async def subscribe_live(self):
        for record in self.records:
            yield record


class _SnapshotView:
    def __init__(self) -> None:
        self.reads = 0

    def snapshot(self) -> object:
        self.reads += 1
        return object()


async def _collect(values) -> list[object]:
    return [value async for value in values]


def test_market_cursor_resets_after_producer_incarnation_change() -> None:
    first = MarketEvent.simulation_bar(
        sequence=8,
        producer_incarnation=1,
        market_id="market:test",
        instrument_id="instrument:test:BTCUSD",
        provider="simulation",
        bar_spec_id="1m",
        open="1",
        high="1",
        low="1",
        close="1",
        occurred_at_unix_nanos=8,
    )
    restarted = MarketEvent.simulation_bar(
        sequence=1,
        producer_incarnation=2,
        market_id="market:test",
        instrument_id="instrument:test:BTCUSD",
        provider="simulation",
        bar_spec_id="1m",
        open="2",
        high="2",
        low="2",
        close="2",
        occurred_at_unix_nanos=9,
    )
    application = MarketApplication(
        None,
        None,
        _Events(first, restarted),
        strategy_id="strategy",
        instance_id="instance",
    )

    asyncio.run(_collect(application.events()))
    assert application._event_cursor == 1
    assert application._event_cursor_key == ("market.events", "market.simulation", 2)
    assert application.notification_health()["incarnation_change_count"] == 1


def test_account_cursor_resyncs_each_account_after_actor_restart() -> None:
    view = _SnapshotView()
    first = AccountEvent.simulation_status("main", "spot", 8)
    restarted = AccountEvent.simulation_valuation(
        "main", "spot", 1, "10", producer_incarnation=2
    )
    application = AccountApplication(
        {AccountId("main"): view}, _Events(first, restarted)
    )

    assert len(asyncio.run(_collect(application._events()))) == 2
    assert view.reads == 0
    assert application.notification_health()["incarnation_change_count"] == 1


def test_risk_cursor_resyncs_from_current_view_after_actor_restart() -> None:
    view = _SnapshotView()
    first = RiskEvent.reservation_changed(8, "main", "strategy")
    restarted = RiskEvent.policy_activated(1, producer_incarnation=2)
    application = RiskApplication(
        view,
        _Events(first, restarted),
        account_ids=(AccountId("main"),),
        strategy_id="strategy",
    )

    events = asyncio.run(_collect(application.events()))
    assert len(events) == 1
    assert view.reads == 0
    assert application.notification_health()["incarnation_change_count"] == 1


def test_execution_cursor_resyncs_from_current_view_after_actor_restart() -> None:
    class _ExecutionView:
        def recovery_snapshot(self):
            return 0, ()

    application = ExecutionApplication(
        None,
        _ExecutionView(),
        _Events(
            ExecutionEvent.simulation_ignored(
                sequence=8,
                producer_incarnation=1,
                instance_id="instance",
                occurred_at_unix_nanos=8,
            ),
            ExecutionEvent.simulation_ignored(
                sequence=1,
                producer_incarnation=2,
                instance_id="instance",
                occurred_at_unix_nanos=9,
            ),
        ),
        strategy_id="strategy",
        instance_id="instance",
    )

    asyncio.run(_collect(application.events()))
    assert application.health()["processing_event_cursor"] == 1
    assert application.health()["notification_incarnation_change_count"] == 1


def test_live_notification_gaps_are_observable_but_not_replay_failures() -> None:
    market = MarketApplication(
        None,
        None,
        _Events(
            MarketEvent.simulation_bar(
                sequence=4,
                market_id="market:test",
                instrument_id="instrument:test:BTCUSD",
                provider="simulation",
                bar_spec_id="1m",
                open="1",
                high="1",
                low="1",
                close="1",
                occurred_at_unix_nanos=4,
            ),
            MarketEvent.simulation_bar(
                sequence=6,
                market_id="market:test",
                instrument_id="instrument:test:BTCUSD",
                provider="simulation",
                bar_spec_id="1m",
                open="2",
                high="2",
                low="2",
                close="2",
                occurred_at_unix_nanos=6,
            ),
        ),
        strategy_id="strategy",
        instance_id="instance",
    )
    account = AccountApplication(
        {AccountId("main"): _SnapshotView()},
        _Events(
            AccountEvent.simulation_status("main", "spot", 4),
            AccountEvent.simulation_status("main", "spot", 6),
        ),
    )
    risk = RiskApplication(
        _SnapshotView(),
        _Events(
            RiskEvent.reservation_changed(4, "main", "strategy"),
            RiskEvent.reservation_changed(6, "main", "strategy"),
        ),
        account_ids=(AccountId("main"),),
        strategy_id="strategy",
    )

    assert len(asyncio.run(_collect(market.events()))) == 2
    assert len(asyncio.run(_collect(account._events()))) == 2
    assert len(asyncio.run(_collect(risk.events()))) == 2
    assert market.notification_health()["gap_count"] == 1
    assert account.notification_health()["gap_count"] == 1
    assert risk.notification_health()["gap_count"] == 1
