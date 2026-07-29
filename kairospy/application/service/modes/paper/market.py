from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.protocol.lines import RuntimeEventLine, close_event_line
from kairospy.application.runtime.services import DataSubscription, MarketDataService, MarketDataSubscriptionSpec
from kairospy.core.market import MarketEvent, OrderBookSnapshot, Quote, TradePrint
from kairospy.core.views import ViewFieldSchema, ViewSchema
from kairospy.infrastructure.integrations.payloads.ccxt_market import (
    ccxt_order_book_update,
    ccxt_ticker_update,
    ccxt_trade_update,
)
from kairospy.infrastructure.integrations.protocols import LiveMarketDataFeed


@dataclass(frozen=True, slots=True)
class PaperMarketDataServiceView:
    source: str
    subscription_count: int = 0
    subscriptions: tuple[DataSubscription, ...] = ()


class PaperMarketDataService(MarketDataService):
    key = "market.service"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("source", "paper market data source", "runtime state", "paper market data service"),
            ViewFieldSchema("subscription_count", "active subscription count", "runtime state", "paper market data service"),
            ViewFieldSchema("subscriptions", "active subscription specs", "runtime state", "paper market data service"),
        ),
        mutability="runtime_writable",
        evidence="runtime paper market data service",
    )

    @classmethod
    def from_feed(cls, feed: LiveMarketDataFeed, *, source_name: str = "paper-live-feed") -> "PaperMarketDataService":
        return cls(feed=feed, source_name=source_name)

    def __init__(self, source: RuntimeEventLine | None = None, *, feed: LiveMarketDataFeed | None = None, source_name: str = "paper") -> None:
        if source is None and feed is None:
            raise ValueError("paper market data service requires a runtime source or integration feed")
        self.source = source
        self.feed = feed
        self.source_name = source_name
        self._subscriptions: dict[str, DataSubscription] = {}
        self._sequence = 0
        self._stop_requested: Callable[[], bool] | None = None

    def set_stop_requested(self, stop_requested: Callable[[], bool] | None) -> None:
        self._stop_requested = stop_requested

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if self.feed is not None and self._subscriptions:
            async for event in self._feed_events():
                yield event
            return
        if self.source is None:
            return
        events = self.source.events()
        try:
            async for event in events:
                yield event
        finally:
            await close_event_line(events)

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        subscription = DataSubscription(spec.key, spec)
        self._subscriptions[subscription.key] = subscription
        return subscription

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        key = subscription if isinstance(subscription, str) else subscription.key
        self._subscriptions.pop(key, None)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return tuple(self._subscriptions[key] for key in sorted(self._subscriptions))

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> PaperMarketDataServiceView:
        subscriptions = self.subscriptions()
        return PaperMarketDataServiceView(self.source_name, len(subscriptions), subscriptions)

    async def _feed_events(self) -> AsyncIterator[RuntimeEnvelope]:
        iterators = [self._subscription_events(subscription).__aiter__() for subscription in self.subscriptions()]
        tasks = {asyncio.create_task(iterator.__anext__()): iterator for iterator in iterators}
        try:
            while tasks and not self._should_stop():
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    iterator = tasks.pop(task)
                    try:
                        yield task.result()
                    except StopAsyncIteration:
                        continue
                    tasks[asyncio.create_task(iterator.__anext__())] = iterator
        finally:
            for task in tasks:
                task.cancel()
            for iterator in iterators:
                await close_event_line(iterator)

    async def _subscription_events(self, subscription: DataSubscription) -> AsyncIterator[RuntimeEnvelope]:
        spec = subscription.spec
        for selector in spec.selectors:
            async for event in self._selector_events(spec, selector):
                yield event

    async def _selector_events(self, spec: MarketDataSubscriptionSpec, selector: object) -> AsyncIterator[RuntimeEnvelope]:
        if self.feed is None:
            return
        model = getattr(selector, "model", None)
        if model is Quote:
            method = getattr(self.feed, "watch_ticker_updates", None)
            stream = method(spec.market.source_symbol, params=spec.params) if callable(method) else self.feed.watch_ticker(spec.market.source_symbol, params=spec.params)
            async for item in stream:
                if self._should_stop():
                    break
                event = item if isinstance(item, MarketEvent) else ccxt_ticker_update(_mapping(item), market=spec.market)
                yield self._envelope("quote", event)
            return
        if model is OrderBookSnapshot:
            depth = getattr(selector, "depth", None)
            method = getattr(self.feed, "watch_order_book_updates", None)
            stream = (
                method(spec.market.source_symbol, limit=depth, params=spec.params)
                if callable(method)
                else self.feed.watch_order_book(spec.market.source_symbol, limit=depth, params=spec.params)
            )
            async for item in stream:
                if self._should_stop():
                    break
                event = item if isinstance(item, MarketEvent) else ccxt_order_book_update(_mapping(item), market=spec.market)
                yield self._envelope("orderbook", event)
            return
        if model is TradePrint:
            method = getattr(self.feed, "watch_trades_updates", None)
            stream = method(spec.market.source_symbol, params=spec.params) if callable(method) else self.feed.watch_trades(spec.market.source_symbol, params=spec.params)
            async for item in stream:
                if self._should_stop():
                    break
                event = item if isinstance(item, MarketEvent) else ccxt_trade_update(_mapping(item), market=spec.market)
                yield self._envelope("trade", event)
            return
        raise ValueError(f"unsupported paper market selector model: {getattr(model, '__name__', model)!r}")

    def _should_stop(self) -> bool:
        return False if self._stop_requested is None else bool(self._stop_requested())

    def _envelope(self, kind: str, event: MarketEvent) -> RuntimeEnvelope:
        self._sequence += 1
        time = event.available_at or event.observed_at
        if time.tzinfo is None:
            time = datetime.now(timezone.utc)
        return RuntimeEnvelope("market", kind, time, self._sequence, event)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("paper market data feed emitted a non-mapping payload")
    return value


__all__ = ["PaperMarketDataService", "PaperMarketDataServiceView"]
