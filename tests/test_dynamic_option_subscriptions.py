from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairospy.application.actor.market.application import MarketActor, ReferenceActor
from kairospy.application.support.messaging import Message
from kairospy.application.support.runtime.domain.commands import RuntimeCommand
from kairospy.infrastructure.messaging import InMemoryMessageBus
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.application.feed import MarketFeedApplicationService
from kairospy.application.usecases.market.application.requests import MarketDataSubscriptionSpec
from kairospy.application.usecases.strategy.protocol import StrategySubscriptionRequest
from kairospy.domain.market import Quote
from kairospy.domain.market.selection import MarketSelection, MarketSelectionQuery
from kairospy.domain.reference import MarketRef


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _Reference:
    def __init__(self, markets: tuple[MarketRef, ...]) -> None:
        self.markets = markets
        self.queries: list[MarketSelectionQuery] = []

    def query(self, request: MarketSelectionQuery) -> MarketSelection:
        self.queries.append(request)
        return MarketSelection(self.markets, NOW, request)


def _selection() -> MarketSelection:
    return MarketSelection(
        (),
        NOW,
        MarketSelectionQuery(venue="massive", market="option", instrument_type="option"),
    )


def _request() -> StrategySubscriptionRequest:
    return StrategySubscriptionRequest(
        subject=_selection(),
        selectors=(Quote,),
        identity="option-strategy",
        dynamic=True,
    )


def test_dynamic_selection_subscribes_current_reference_members() -> None:
    first = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00500000")
    second = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00510000")
    reference = _Reference((first, second))
    market = MarketApplication()
    actor = MarketActor(None, None, market_service=market, reference=reference)  # type: ignore[arg-type]

    result = actor.call(RuntimeCommand("market.subscribe.dynamic", _request()))

    assert result.accepted
    assert len(market.subscriptions()) == 2
    assert {item.spec.market.source_symbol for item in market.subscriptions()} == {
        first.source_symbol,
        second.source_symbol,
    }
    assert reference.queries[0].as_of is None


def test_dynamic_selection_rejects_more_contracts_than_market_budget() -> None:
    first = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00500000")
    second = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00510000")
    reference = _Reference((first, second))
    actor = MarketActor(
        None,
        None,
        market_service=MarketApplication(),
        reference=reference,
        max_dynamic_members=1,
    )  # type: ignore[arg-type]

    result = actor.call(RuntimeCommand("market.subscribe.dynamic", _request()))

    assert not result.accepted
    assert "limit is 1" in (result.error or "")


def test_reference_update_over_budget_keeps_the_previous_dynamic_subscription() -> None:
    first = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00500000")
    second = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00510000")
    third = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00520000")
    reference = _Reference((first, second))
    market = MarketApplication()
    actor = MarketActor(
        None,
        None,
        market_service=market,
        reference=reference,
        max_dynamic_members=2,
    )  # type: ignore[arg-type]

    result = actor.call(RuntimeCommand("market.subscribe.dynamic", _request()))
    assert result.accepted
    reference.markets = (first, second, third)

    actor._reconcile_dynamic_subscriptions()

    assert {item.spec.market.source_symbol for item in market.subscriptions()} == {
        first.source_symbol,
        second.source_symbol,
    }


def test_reference_change_reconciles_dynamic_members_without_touching_static_subscription() -> None:
    first = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00500000")
    second = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00510000")
    third = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00520000")
    reference = _Reference((first, second))
    market = MarketApplication()
    actor = MarketActor(None, None, market_service=market, reference=reference)  # type: ignore[arg-type]

    dynamic = actor.call(RuntimeCommand("market.subscribe.dynamic", _request()))
    static = market.subscribe(
        MarketDataSubscriptionSpec(
            third,
            (Quote,),
            identity="static-position",
        )
    )

    reference.markets = (second, third)
    asyncio.run(
        actor.process(
            Message(
                topic="reference.catalog.changed",
                payload={},
                published_at=NOW,
                producer="reference",
                producer_sequence=1,
            )
        )
    )

    assert dynamic.accepted
    assert market.subscriptions() == tuple(sorted(market.subscriptions(), key=lambda item: item.key))
    assert {item.spec.market.source_symbol for item in market.subscriptions()} == {
        second.source_symbol,
        third.source_symbol,
    }
    assert static.key in {item.key for item in market.subscriptions()}


def test_repeated_reference_change_is_idempotent_and_dynamic_unsubscribe_cleans_members() -> None:
    first = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00500000")
    second = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00510000")
    reference = _Reference((first, second))
    market = MarketApplication()
    actor = MarketActor(None, None, market_service=market, reference=reference)  # type: ignore[arg-type]

    result = actor.call(RuntimeCommand("market.subscribe.dynamic", _request()))
    actor._reconcile_dynamic_subscriptions()
    assert len(market.subscriptions()) == 2

    actor.call(RuntimeCommand("market.unsubscribe", result.result["subscription_id"]))

    assert market.subscriptions() == ()
    assert not market.has_subscription_intents()


def test_reference_actor_has_independent_lifecycle_from_market_actor() -> None:
    bus = InMemoryMessageBus()
    reference = _Reference(())
    market = MarketApplication()

    reference_actor = ReferenceActor(reference, bus, poll_interval_seconds=60)  # type: ignore[arg-type]
    market_actor = MarketActor(None, bus, market_service=market, reference=reference)  # type: ignore[arg-type]

    assert reference_actor.name == "reference"
    assert not hasattr(market_actor, "reference_actor")


class _Remote:
    def __init__(self, subscription_id: str) -> None:
        self.subscription_id = subscription_id
        self.queue: asyncio.Queue[object] = asyncio.Queue()

    def events(self):
        return self._events()

    async def _events(self):
        while True:
            yield await self.queue.get()


class _Connection:
    venue = "massive"

    def __init__(self) -> None:
        self.remotes: dict[str, _Remote] = {}
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    async def subscribe(self, request):
        remote = _Remote(f"remote-{len(self.remotes) + 1}")
        self.remotes[remote.subscription_id] = remote
        self.subscribed.append(request.market.source_symbol)
        return remote

    async def unsubscribe(self, subscription_id: str) -> None:
        self.unsubscribed.append(subscription_id)
        self.remotes.pop(subscription_id, None)


def test_market_feed_applies_subscription_additions_and_removals_while_running() -> None:
    async def scenario() -> None:
        first = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00500000")
        second = MarketRef.ephemeral(venue="massive", market="option", source_symbol="O:SPY260821P00510000")
        market = MarketApplication()
        market.subscribe(MarketDataSubscriptionSpec(first, (Quote,), identity="dynamic"))
        connection = _Connection()
        feed = MarketFeedApplicationService(market, stream_connections={"massive": connection})
        events = feed.events()
        task = asyncio.create_task(events.__anext__())
        for _ in range(20):
            if connection.subscribed:
                break
            await asyncio.sleep(0.01)
        market.subscribe(MarketDataSubscriptionSpec(second, (Quote,), identity="dynamic"))
        for _ in range(80):
            if len(connection.subscribed) == 2:
                break
            await asyncio.sleep(0.01)
        market.unsubscribe(market.subscriptions()[0])
        for _ in range(80):
            if connection.unsubscribed:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await events.aclose()
        assert connection.subscribed == [first.source_symbol, second.source_symbol]
        assert connection.unsubscribed == ["remote-1", "remote-2"]

    asyncio.run(scenario())
