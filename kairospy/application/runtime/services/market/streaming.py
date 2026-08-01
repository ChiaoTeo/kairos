from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Callable, Mapping

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.protocol.lines import RuntimeEventLine, close_event_line
from kairospy.application.ports import DataSubscription, MarketDataSubscriptionSpec, MarketStreamGateway
from kairospy.application.runtime.connections import ConnectionManager
from kairospy.application.runtime.contracts import MarketRuntime, MarketRuntimeEnvelope
from kairospy.core.market import MarketEvent, OptionGreeks, OrderBookSnapshot, Quote, TradePrint

from .common import MarketSubscriptionState, RuntimeMarketDataServiceView

MarketFeedResolver = Callable[[MarketDataSubscriptionSpec], MarketStreamGateway | None]


class StreamingMarketDataService(MarketSubscriptionState, MarketRuntime):
    def __init__(
        self,
        source: RuntimeEventLine | None = None,
        *,
        feed: MarketStreamGateway | None = None,
        feed_resolver: MarketFeedResolver | None = None,
        source_name: str,
        mode_label: str = "streaming",
        connections: ConnectionManager | None = None,
    ) -> None:
        if source is None and feed is None and feed_resolver is None:
            raise ValueError(f"{mode_label} market data service requires a runtime source or integration feed")
        super().__init__()
        self.source = source
        self.feed = feed
        self.feed_resolver = feed_resolver
        self.source_name = source_name
        self.mode_label = mode_label
        self.connections = connections
        self._sequence = 0
        self._stop_requested: Callable[[], bool] | None = None
        if self.feed is not None and self.connections is not None:
            self.connections.register(f"{self.mode_label}.market_feed.default", self.feed, role="market_feed")

    def set_stop_requested(self, stop_requested: Callable[[], bool] | None) -> None:
        self._stop_requested = stop_requested

    def set_connection_manager(self, connections: ConnectionManager | None) -> None:
        self.connections = connections
        if self.feed is not None and self.connections is not None:
            self.feed = self.connections.register(f"{self.mode_label}.market_feed.default", self.feed, role="market_feed")

    async def events(self) -> AsyncIterator[MarketRuntimeEnvelope]:
        if (self.feed is not None or self.feed_resolver is not None) and self._subscriptions:
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

    def view(self) -> RuntimeMarketDataServiceView:
        subscriptions = self.subscriptions()
        return RuntimeMarketDataServiceView(self.source_name, len(subscriptions), subscriptions)

    async def _feed_events(self) -> AsyncIterator[MarketRuntimeEnvelope]:
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
            if tasks:
                await asyncio.gather(*tasks.keys(), return_exceptions=True)
            for iterator in iterators:
                await close_event_line(iterator)

    async def _subscription_events(self, subscription: DataSubscription) -> AsyncIterator[MarketRuntimeEnvelope]:
        spec = subscription.spec
        for selector in spec.selectors:
            async for event in self._selector_events(spec, selector):
                yield event

    async def _selector_events(self, spec: MarketDataSubscriptionSpec, selector: object) -> AsyncIterator[MarketRuntimeEnvelope]:
        model = getattr(selector, "model", None)
        source_symbol = str(spec.market.source_symbol)
        if model is Quote:
            async for item in self._watch_with_reconnect(
                spec,
                lambda feed: feed.watch_ticker_updates(source_symbol, market=spec.market, params=spec.params),
            ):
                yield self._envelope("quote", item)
            return
        if model is OrderBookSnapshot:
            depth = getattr(selector, "depth", None)
            params = dict(spec.params)
            derivation = getattr(selector, "derivation", "direct")
            if derivation != "direct":
                params["derivation"] = derivation
            if depth == "full":
                params["orderbook_depth"] = "full"
                depth = None
            async for item in self._watch_with_reconnect(
                spec,
                lambda feed: feed.watch_order_book_updates(source_symbol, market=spec.market, limit=depth, params=params),
            ):
                yield self._envelope("orderbook", item)
            return
        if model is TradePrint:
            async for item in self._watch_with_reconnect(
                spec,
                lambda feed: feed.watch_trades_updates(source_symbol, market=spec.market, params=spec.params),
            ):
                yield self._envelope("trade", item)
            return
        if model is OptionGreeks:
            async for item in self._watch_with_reconnect(
                spec,
                lambda feed: feed.watch_option_greeks_updates(source_symbol, market=spec.market, params=spec.params),
            ):
                yield self._envelope("option_greeks", item)
            return
        raise ValueError(f"unsupported {self.mode_label} market selector model: {getattr(model, '__name__', model)!r}")

    async def _watch_with_reconnect(
        self,
        spec: MarketDataSubscriptionSpec,
        watch: Callable[[MarketStreamGateway], AsyncIterator[MarketEvent]],
    ) -> AsyncIterator[MarketEvent]:
        attempts = 0
        while attempts < 2 and not self._should_stop():
            key = self._feed_connection_key(spec)
            feed = self._feed_for(spec)
            if feed is None:
                return
            try:
                async for item in watch(feed):
                    if self._should_stop():
                        break
                    yield item
                return
            except Exception as error:
                attempts += 1
                if self.connections is None or attempts >= 2:
                    raise
                recorder = getattr(self.connections, "record_error", None)
                if callable(recorder):
                    recorder(key, error)
                self.connections.reconnect(key)

    def _should_stop(self) -> bool:
        return False if self._stop_requested is None else bool(self._stop_requested())

    def _feed_for(self, spec: MarketDataSubscriptionSpec) -> MarketStreamGateway | None:
        if self.feed_resolver is not None:
            if self.connections is not None:
                return self.connections.resolve(
                    self._feed_connection_key(spec),
                    role="market_feed",
                    factory=lambda: self.feed_resolver(spec) or self.feed,
                )
            return self.feed_resolver(spec) or self.feed
        return self.feed

    def _feed_connection_key(self, spec: MarketDataSubscriptionSpec) -> str:
        return f"{self.mode_label}.market_feed.{spec.market.venue}.{spec.market.market}"

    def _envelope(self, kind: str, event: MarketEvent) -> MarketRuntimeEnvelope:
        self._sequence += 1
        time = event.available_at or event.observed_at
        if time.tzinfo is None:
            time = datetime.now(timezone.utc)
        return RuntimeEnvelope("market", kind, time, self._sequence, event)

__all__ = ["MarketFeedResolver", "StreamingMarketDataService"]
