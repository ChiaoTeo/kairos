"""Public data capability for the market usecase."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from pathlib import Path

from kairospy.application.usecases.market.domain.datasets import MarketPartition, parse_market_dataset_id
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.domain.subscriptions import (
    DataSubscription,
    DataSubscriptionGroup,
    MarketDataSubscriptionGroupSpec,
    MarketDataSubscriptionSpec,
)
from kairospy.application.usecases.market.services.operations import MarketDataOperationsService
from kairospy.application.usecases.market.services.resolver import MarketDataResolver, ResolvedMarketData
from kairospy.application.usecases.market.application.integration import MarketDataConnectionRequest, MarketIntegrationRuntime


class MarketDataApplicationService:
    """Narrow market capability used by composed market business tasks."""

    def __init__(
        self,
        store: object | None = None,
        *,
        resolver: MarketDataResolver | None = None,
        integration_runtime: MarketIntegrationRuntime | None = None,
    ) -> None:
        self._operations = None if store is None else MarketDataOperationsService(store, resolver=resolver)
        self._resolver = resolver or MarketDataResolver()
        self._integration_runtime = integration_runtime

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        return self._require_operations().resolve(spec)

    @property
    def store(self) -> object:
        return self._require_operations().store

    @property
    def resolver(self) -> MarketDataResolver:
        return self._resolver

    def read(self, spec: MarketDataSpec, *, columns: Iterable[str] | None = None) -> list[dict[str, object]]:
        return self._require_operations().read(spec, columns=columns)

    def download(self, spec: MarketDataSpec, client: object | None = None, *, mode: str = "append", options: Mapping[str, object] | None = None) -> Path:
        if client is None:
            client = self._require_integration().create_data(MarketDataConnectionRequest(spec, params=options or {}))
        return self._require_operations().download(spec, client, mode=mode, params=options)

    def persist_historical(self, spec: MarketDataSpec, observations: Iterable[object], *, mode: str = "append") -> Path:
        return self._require_operations().persist_historical(spec, observations, mode=mode)

    def ensure(
        self,
        spec: MarketDataSpec,
        client: object | None = None,
        *,
        mode: str = "append",
        options: Mapping[str, object] | None = None,
    ) -> ResolvedMarketData:
        if client is None:
            client = self._require_integration().create_data(MarketDataConnectionRequest(spec, params=options or {}))
        return self._require_operations().ensure(spec, client, mode=mode, params=options)

    async def persist(
        self,
        spec: MarketDataSpec,
        events: AsyncIterable[Mapping[str, object]],
        *,
        limit: int | None = None,
    ) -> int:
        return await self._require_operations().persist(spec, events, limit=limit)

    def partition_for(self, resolved: ResolvedMarketData) -> MarketPartition:
        return self._require_operations().partition_for(resolved)

    def partition_for_spec(self, spec: MarketDataSpec) -> MarketPartition:
        return self._require_operations().partition_for_spec(spec)

    def _require_operations(self) -> MarketDataOperationsService:
        if self._operations is None:
            raise RuntimeError("market data application requires a dataset store for data operations")
        return self._operations

    def _require_integration(self) -> MarketIntegrationRuntime:
        if self._integration_runtime is None:
            raise RuntimeError("market data operation requires a MarketIntegrationRuntime")
        return self._integration_runtime

__all__ = [
    "DataSubscription",
    "DataSubscriptionGroup",
    "MarketDataApplicationService",
    "MarketDataSpec",
    "MarketDataSubscriptionSpec",
    "MarketDataSubscriptionGroupSpec",
    "MarketPartition",
    "parse_market_dataset_id",
    "ResolvedMarketData",
]
