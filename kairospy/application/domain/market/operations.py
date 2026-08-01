from __future__ import annotations

from collections.abc import AsyncIterable, Iterable
from pathlib import Path
from typing import Mapping

from kairospy.application.ports import HistoricalMarketDataPort, MarketDatasetStore, MarketPartition

from .datasets import parse_market_dataset_id
from .resolver import MarketDataResolver, ResolvedMarketData
from .specs import MarketDataSpec
from .subscriptions import MarketSubscription


class MarketDataOperationsService:
    def __init__(self, store: MarketDatasetStore, resolver: MarketDataResolver | None = None) -> None:
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
            partition=self.partition_for(resolved),
        )

    def download(
        self,
        spec: MarketDataSpec,
        client: HistoricalMarketDataPort,
        *,
        mode: str = "append",
        params: Mapping[str, object] | None = None,
    ) -> Path:
        if spec.kind not in {"ohlcv", "funding_rate"}:
            raise ValueError(f"unsupported historical data kind: {spec.kind}")
        resolved = self.resolve(spec)
        effective_params = {"market": str(resolved.market_ref.market), "type": str(resolved.market_ref.market), **dict(params or {})}
        if spec.kind == "funding_rate":
            rates = client.fetch_funding_rates(
                resolved.market_ref.source_symbol,
                since=spec.start,
                until=spec.end,
                limit=spec.limit or 1000,
                adapter_options=effective_params,
            )
            return self.store.write_funding_rates(
                resolved.dataset_id,
                rates,
                market=resolved.market_ref,
                mode=mode,
                partition=self.partition_for(resolved),
            )
        else:
            bars = client.fetch_bars(
                resolved.market_ref.source_symbol,
                timeframe=spec.timeframe or "1m",
                since=spec.start,
                until=spec.end,
                limit=spec.limit or 1000,
                adapter_options=effective_params,
            )
            return self.store.write_bars(
                resolved.dataset_id,
                bars,
                market=resolved.market_ref,
                mode=mode,
                partition=self.partition_for(resolved),
            )

    def ensure(
        self,
        spec: MarketDataSpec,
        client: HistoricalMarketDataPort | None = None,
        *,
        mode: str = "append",
        params: Mapping[str, object] | None = None,
    ) -> ResolvedMarketData:
        resolved = self.resolve(spec)
        if self.read(MarketDataSpec(
            spec.symbol,
            spec.kind,
            venue=spec.venue,
            market=spec.market,
            timeframe=spec.timeframe,
            start=spec.start,
            end=spec.end,
            limit=1,
            dataset=spec.dataset,
            stream=spec.stream,
        )):
            return resolved
        if client is None:
            raise RuntimeError(f"dataset has no rows and no client was provided: {resolved.dataset_id}")
        self.download(spec, client, mode=mode, params=params)
        return resolved

    async def persist(
        self,
        spec: MarketDataSpec,
        events: AsyncIterable[Mapping[str, object]],
        *,
        limit: int | None = None,
    ) -> int:
        resolved = self.resolve(spec)
        consume = getattr(self.store, "consume", None)
        if callable(consume):
            return await consume(resolved.dataset_id, events, partition=self.partition_for(resolved), limit=limit)
        count = 0
        async for event in events:
            self.store.write(resolved.dataset_id, (event,), partition=self.partition_for(resolved))
            count += 1
            if limit is not None and count >= limit:
                break
        return count

    def subscription_stream(self, spec: MarketDataSpec) -> str:
        return self.resolve(spec).stream_name

    def spec_from_subscription(self, subscription: MarketSubscription) -> MarketDataSpec:
        dataset_id = getattr(subscription, "dataset_id", None)
        if dataset_id is not None:
            dataset = parse_market_dataset_id(dataset_id)
            return MarketDataSpec(
                symbol=dataset.source_symbol,
                kind=dataset.kind,
                venue=dataset.venue,
                market=dataset.market,
                timeframe=dataset.timeframe,
                dataset=dataset.dataset_id,
            )
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

    def partition_for(self, resolved: ResolvedMarketData) -> MarketPartition:
        return market_partition_for(kind=resolved.spec.kind, timeframe=resolved.spec.timeframe)


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
    if "OptionGreeks" in models:
        return "option_greeks"
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


def market_partition_for(*, kind: str, timeframe: str | None = None) -> MarketPartition:
    normalized_kind = kind.strip().lower()
    if normalized_kind in {"trades", "trade", "orderbook", "order_book"}:
        return MarketPartition(time_grain="day")
    if normalized_kind in {"ticker", "quote", "option_greeks", "greeks"}:
        return MarketPartition(time_grain="day")
    if normalized_kind == "funding_rate":
        return MarketPartition(time_grain="month")
    if normalized_kind != "ohlcv":
        return MarketPartition()
    seconds = _timeframe_seconds(timeframe)
    if seconds is None:
        return MarketPartition()
    if seconds <= 5 * 60:
        return MarketPartition(time_grain="day")
    if seconds <= 60 * 60:
        return MarketPartition(time_grain="month")
    return MarketPartition(time_grain="year")


def _timeframe_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().lower()
    if len(text) < 2:
        return None
    unit = text[-1]
    try:
        amount = int(text[:-1])
    except ValueError:
        return None
    if amount <= 0:
        return None
    if unit == "s":
        return amount
    if unit == "m":
        return amount * 60
    if unit == "h":
        return amount * 60 * 60
    if unit == "d":
        return amount * 24 * 60 * 60
    if unit == "w":
        return amount * 7 * 24 * 60 * 60
    return None


__all__ = ["MarketDataOperationsService", "market_partition_for"]
