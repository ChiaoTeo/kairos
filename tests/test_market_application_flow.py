from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.usecases.market.application.events import MarketEventApplicationService
from kairospy.application.usecases.market.application.feed import MarketFeedApplicationService
from kairospy.application.usecases.market.application.runtime import build_replay_market
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.application.requests import MarketDataSubscriptionSpec
from kairospy.domain.market import MarketEvent, MarketSubject, Quote
from kairospy.domain.reference import MarketRef


def _event() -> MarketEvent:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    quote = Quote("BTCUSDT", at, bid=Decimal("1"), ask=Decimal("2"), source="test")
    return MarketEvent(MarketSubject("instrument", "BTCUSDT"), at, quote, source="test", sequence=1)


def test_market_event_application_processes_one_canonical_event() -> None:
    seen: list[MarketEvent] = []

    class Ingestion:
        def event_from_message(self, message: object) -> MarketEvent:
            return message  # type: ignore[return-value]

    class Projection:
        def apply(self, event: MarketEvent) -> None:
            seen.append(event)

    class Sink:
        def __init__(self) -> None:
            self.events: list[MarketEvent] = []

        def append(self, event: MarketEvent) -> None:
            self.events.append(event)

    sink = Sink()
    service = MarketEventApplicationService(ingestion=Ingestion(), projection=Projection(), sink=sink)
    event = _event()

    assert service.handle(event) is event
    assert seen == [event]
    assert sink.events == [event]


def test_market_feed_subscribe_requests_remote_data_and_emits_canonical_events() -> None:
    async def scenario() -> None:
        stopped = False
        unsubscribed: list[str] = []
        event = _event()

        class Remote:
            subscription_id = "remote-1"

            async def events(self):
                yield event
                while not stopped:
                    await asyncio.sleep(0.01)

        class Feed:
            venue = "binance"

            async def subscribe(self, request):
                assert request.identity.startswith("data.")
                return Remote()

            async def unsubscribe(self, subscription_id: str) -> None:
                unsubscribed.append(subscription_id)

        market = MarketApplication()
        feed = MarketFeedApplicationService(market, feed=Feed())
        feed.subscribe(
            MarketDataSubscriptionSpec(
                MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTCUSDT"),
                (Quote,),
            )
        )

        stream = feed.events()
        assert await anext(stream) is event
        await stream.aclose()
        assert unsubscribed == ["remote-1"]

    asyncio.run(scenario())


def test_replay_source_uses_system_subscription_state_when_bound() -> None:
    class Store:
        pass

    class Subscriptions:
        def __init__(self) -> None:
            self.items = []

        def subscribe(self, spec):
            self.items.append(spec)
            return spec

        def unsubscribe(self, subscription):
            self.items.remove(subscription)

        def subscriptions(self):
            return tuple(self.items)

    class Market:
        def __init__(self) -> None:
            self._subscriptions = Subscriptions()

        def subscribe(self, spec):
            return self._subscriptions.subscribe(spec)

        def unsubscribe(self, subscription):
            self._subscriptions.unsubscribe(subscription)

        def subscriptions(self):
            return self._subscriptions.subscriptions()

    source = build_replay_market(Store())
    market = Market()
    source.set_market_service(market)
    spec = MarketDataSubscriptionSpec(
        MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTCUSDT"),
        (Quote,),
    )

    assert source.subscribe(spec) is spec
    assert source.subscriptions() == (spec,)
