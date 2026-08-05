"""Public market data operations."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from pathlib import Path

from kairospy.application.usecases.market.services.operations import MarketDataOperationsService as _MarketDataOperationsService, market_partition_for
from kairospy.application.usecases.market.application.resolver import MarketDataResolver, ResolvedMarketData
from kairospy.application.usecases.market.domain.datasets import MarketPartition
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.application.requests import MarketDataRow, MarketOptions
from kairospy.application.usecases.market.protocol import MarketDataStore, MarketHistoricalClient
from kairospy.domain.market import MarketEvent
from kairospy.application.usecases.market.domain.subscriptions import DataSubscription


class MarketDataOperationsService:
    def __init__(self, store: MarketDataStore, resolver: MarketDataResolver | None = None) -> None:
        self._service = _MarketDataOperationsService(store, resolver=resolver)

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        return self._service.resolve(spec)

    def read(self, spec: MarketDataSpec, *, columns: Iterable[str] | None = None) -> list[MarketDataRow]:
        return self._service.read(spec, columns=columns)

    def download(self, spec: MarketDataSpec, client: MarketHistoricalClient, *, mode: str = "append", options: MarketOptions | None = None) -> Path:
        return self._service.download(spec, client, mode=mode, params=options)

    def ensure(self, spec: MarketDataSpec, client: MarketHistoricalClient | None = None, *, mode: str = "append", options: MarketOptions | None = None) -> ResolvedMarketData:
        return self._service.ensure(spec, client, mode=mode, params=options)

    async def persist(self, spec: MarketDataSpec, events: AsyncIterable[MarketEvent], *, limit: int | None = None) -> int:
        return await self._service.persist(spec, events, limit=limit)

    def subscription_stream(self, spec: MarketDataSpec) -> str:
        return self._service.subscription_stream(spec)

    def spec_from_subscription(self, subscription: DataSubscription) -> MarketDataSpec:
        return self._service.spec_from_subscription(subscription)

    def resolve_subscription(self, subscription: DataSubscription) -> ResolvedMarketData:
        return self._service.resolve_subscription(subscription)

    def partition_for(self, resolved: ResolvedMarketData) -> MarketPartition:
        return self._service.partition_for(resolved)


__all__ = ["MarketDataOperationsService", "market_partition_for"]
