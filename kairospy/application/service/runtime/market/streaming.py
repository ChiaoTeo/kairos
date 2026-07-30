from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Callable, Mapping

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.protocol.lines import RuntimeEventLine, close_event_line
from kairospy.application.ports import DataSubscription, MarketDataPort, MarketDataSubscriptionSpec
from kairospy.core.market import MarketEvent, OptionGreeks, OrderBookSnapshot, Quote, TradePrint
from kairospy.infrastructure.integrations.payloads.ccxt_market import (
    ccxt_order_book_update,
    ccxt_option_greeks_update,
    ccxt_ticker_update,
    ccxt_trade_update,
)
from kairospy.infrastructure.integrations.protocols import LiveMarketDataFeed

from .common import MarketSubscriptionState, RuntimeMarketDataServiceView


class StreamingMarketDataService(MarketSubscriptionState, MarketDataPort):
    def __init__(
        self,
        source: RuntimeEventLine | None = None,
        *,
        feed: LiveMarketDataFeed | None = None,
        source_name: str,
        mode_label: str = "streaming",
    ) -> None:
        if source is None and feed is None:
            raise ValueError(f"{mode_label} market data service requires a runtime source or integration feed")
        super().__init__()
        self.source = source
        self.feed = feed
        self.source_name = source_name
        self.mode_label = mode_label
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

    def view(self) -> RuntimeMarketDataServiceView:
        subscriptions = self.subscriptions()
        return RuntimeMarketDataServiceView(self.source_name, len(subscriptions), subscriptions)

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
            if tasks:
                await asyncio.gather(*tasks.keys(), return_exceptions=True)
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
        source_symbol = str(spec.market.source_symbol)
        if model is Quote:
            method = getattr(self.feed, "watch_ticker_updates", None)
            stream = method(source_symbol, params=spec.params) if callable(method) else self.feed.watch_ticker(source_symbol, params=spec.params)
            async for item in stream:
                if self._should_stop():
                    break
                event = item if isinstance(item, MarketEvent) else ccxt_ticker_update(_mapping(item, mode_label=self.mode_label), market=spec.market)
                yield self._envelope("quote", event)
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
            method = getattr(self.feed, "watch_order_book_updates", None)
            stream = (
                method(source_symbol, limit=depth, params=params)
                if callable(method)
                else self.feed.watch_order_book(source_symbol, limit=depth, params=params)
            )
            async for item in stream:
                if self._should_stop():
                    break
                event = item if isinstance(item, MarketEvent) else ccxt_order_book_update(_mapping(item, mode_label=self.mode_label), market=spec.market)
                yield self._envelope("orderbook", event)
            return
        if model is TradePrint:
            method = getattr(self.feed, "watch_trades_updates", None)
            stream = method(source_symbol, params=spec.params) if callable(method) else self.feed.watch_trades(source_symbol, params=spec.params)
            async for item in stream:
                if self._should_stop():
                    break
                event = item if isinstance(item, MarketEvent) else ccxt_trade_update(_mapping(item, mode_label=self.mode_label), market=spec.market)
                yield self._envelope("trade", event)
            return
        if model is OptionGreeks:
            method = getattr(self.feed, "watch_option_greeks_updates", None)
            if callable(method):
                stream = method(source_symbol, params=spec.params)
            else:
                stream = self.feed.watch_option_greeks(source_symbol, params=spec.params)  # type: ignore[attr-defined]
            async for item in stream:
                if self._should_stop():
                    break
                event = item if isinstance(item, MarketEvent) else ccxt_option_greeks_update(_mapping(item, mode_label=self.mode_label), market=spec.market)
                yield self._envelope("option_greeks", event)
            return
        raise ValueError(f"unsupported {self.mode_label} market selector model: {getattr(model, '__name__', model)!r}")

    def _should_stop(self) -> bool:
        return False if self._stop_requested is None else bool(self._stop_requested())

    def _envelope(self, kind: str, event: MarketEvent) -> RuntimeEnvelope:
        self._sequence += 1
        time = event.available_at or event.observed_at
        if time.tzinfo is None:
            time = datetime.now(timezone.utc)
        return RuntimeEnvelope("market", kind, time, self._sequence, event)


def _mapping(value: object, *, mode_label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{mode_label} market data feed emitted a non-mapping payload")
    return value


__all__ = ["StreamingMarketDataService"]
