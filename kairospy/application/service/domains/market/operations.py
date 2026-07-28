from __future__ import annotations

from collections.abc import AsyncIterable, Iterable
from pathlib import Path
from typing import Mapping, Protocol

from kairospy.infrastructure.data import DataSink, DataStore

from .resolver import MarketDataResolver, ResolvedMarketData
from .specs import MarketDataSpec
from .subscriptions import MarketSubscription


class HistoricalMarketDataClient(Protocol):
    def fetch_ohlcv(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
    ) -> Iterable[Mapping[str, object]]:
        ...


class MarketDataService:
    def __init__(self, store: DataStore, resolver: MarketDataResolver | None = None) -> None:
        self.store = store
        self.resolver = resolver or MarketDataResolver()

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        return self.resolver.resolve(spec)

    def read(self, spec: MarketDataSpec, *, columns: Iterable[str] | None = None) -> list[dict[str, object]]:
        resolved = self.resolve(spec)
        return self.store.read_rows(
            resolved.dataset_id,
            start=spec.start,
            end=spec.end,
            columns=columns,
            limit=spec.limit,
        )

    def download(
        self,
        spec: MarketDataSpec,
        client: HistoricalMarketDataClient,
        *,
        mode: str = "append",
    ) -> Path:
        if spec.kind != "ohlcv":
            raise ValueError(f"unsupported historical data kind: {spec.kind}")
        resolved = self.resolve(spec)
        rows = client.fetch_ohlcv(
            resolved.market_ref.source_symbol,
            timeframe=spec.timeframe or "1m",
            since=spec.start,
            until=spec.end,
            limit=spec.limit or 1000,
        )
        return self.store.write(resolved.dataset_id, rows, mode=mode)

    def ensure(
        self,
        spec: MarketDataSpec,
        client: HistoricalMarketDataClient | None = None,
        *,
        mode: str = "append",
    ) -> ResolvedMarketData:
        resolved = self.resolve(spec)
        if self.store.read_rows(resolved.dataset_id, start=spec.start, end=spec.end, limit=1):
            return resolved
        if client is None:
            raise RuntimeError(f"dataset has no rows and no client was provided: {resolved.dataset_id}")
        self.download(spec, client, mode=mode)
        return resolved

    def sink(self, spec: MarketDataSpec) -> DataSink:
        resolved = self.resolve(spec)
        return DataSink(self.store, resolved.dataset_id)

    async def persist(
        self,
        spec: MarketDataSpec,
        events: AsyncIterable[Mapping[str, object]],
        *,
        limit: int | None = None,
    ) -> int:
        return await self.sink(spec).consume(events, limit=limit)

    def subscription_stream(self, spec: MarketDataSpec) -> str:
        return self.resolve(spec).stream_name

    def spec_from_subscription(self, subscription: MarketSubscription) -> MarketDataSpec:
        kind = _kind_from_subscription(subscription)
        timeframe = _timeframe_from_subscription(subscription)
        symbol = subscription.source_symbol or subscription.market_id or subscription.instrument_id
        return MarketDataSpec(
            symbol=symbol,
            kind=kind,
            venue=subscription.venue or None,
            market=subscription.market or None,
            timeframe=timeframe,
        )

    def resolve_subscription(self, subscription: MarketSubscription) -> ResolvedMarketData:
        return self.resolve(self.spec_from_subscription(subscription))


def _kind_from_subscription(subscription: MarketSubscription) -> str:
    models = {selector.model.__name__ for selector in subscription.spec.selectors}
    if "Bar" in models:
        return "ohlcv"
    if "TradePrint" in models:
        return "trades"
    if "OrderBookSnapshot" in models:
        return "orderbook"
    if "Quote" in models:
        return "ticker"
    if models:
        return sorted(models)[0].lower()
    return subscription.kind


def _timeframe_from_subscription(subscription: MarketSubscription) -> str | None:
    intervals = {
        selector.interval
        for selector in subscription.spec.selectors
        if selector.interval is not None
    }
    if len(intervals) > 1:
        raise ValueError(f"market subscription spans multiple intervals: {sorted(intervals)}")
    return next(iter(intervals), None)


__all__ = ["HistoricalMarketDataClient", "MarketDataService"]
