from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from kairospy.application.usecases.market.services.operations import MarketDataOperationsService, market_partition_for
from kairospy.application.usecases.market.services.projections import MarketProjectionService
from kairospy.application.usecases.market.services.replay import (
    HistoricalClientFactory,
    MarketReplayService,
    ReplayMarketDataPolicy,
    replay_rows,
    specs_from_subscription,
)
from kairospy.application.usecases.market.services.resolver import MarketDataResolver, ResolvedMarketData
from kairospy.application.usecases.market.services.ingestion import MarketIngestionService
from kairospy.application.usecases.market.services.sources import IterableMarketEventSource
from kairospy.application.usecases.market.domain.datasets import MarketPartition
from kairospy.application.usecases.market.domain.datasets import parse_market_dataset_id
from kairospy.application.usecases.market.domain.planning import MarketFeedWatchPlan, MarketStreamPlanningService
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.domain.subscriptions import DataSubscription, MarketDataSubscriptionSpec, MarketSubscriptionService


class MarketDataService:
    """Application facade for market data use cases."""

    def __init__(
        self,
        store: object | None = None,
        *,
        resolver: MarketDataResolver | None = None,
        subscriptions: MarketSubscriptionService | None = None,
        planning: MarketStreamPlanningService | None = None,
        policy: ReplayMarketDataPolicy | None = None,
        historical_client: object | None = None,
        historical_client_factory: HistoricalClientFactory | None = None,
    ) -> None:
        self.operations = None if store is None else MarketDataOperationsService(store, resolver=resolver)
        self.resolver = resolver or MarketDataResolver()
        self.subscriptions_service = subscriptions or MarketSubscriptionService()
        self.planning = planning or MarketStreamPlanningService()
        self.replay = (
            None
            if self.operations is None
            else MarketReplayService(
                self.operations,
                policy=policy,
                historical_client=historical_client,
                historical_client_factory=historical_client_factory,
            )
        )

    @property
    def store(self) -> object:
        return self._operations().store

    def set_historical_client_factory(self, factory: HistoricalClientFactory | None) -> None:
        replay = self._replay()
        replay.set_historical_client_factory(factory)

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        return self._operations().resolve(spec)

    def read(self, spec: MarketDataSpec, *, columns: Iterable[str] | None = None) -> list[dict[str, object]]:
        return self._operations().read(spec, columns=columns)

    def download(
        self,
        spec: MarketDataSpec,
        client: object,
        *,
        mode: str = "append",
        params: Mapping[str, object] | None = None,
    ) -> Path:
        return self._operations().download(spec, client, mode=mode, params=params)

    def partition_for(self, resolved: ResolvedMarketData) -> MarketPartition:
        return self._operations().partition_for(resolved)

    def partition_for_spec(self, spec: MarketDataSpec) -> MarketPartition:
        return market_partition_for(kind=spec.kind, timeframe=spec.timeframe)

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        return self.subscriptions_service.subscribe(spec)

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        self.subscriptions_service.unsubscribe(subscription)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return self.subscriptions_service.subscriptions()

    def rows_for_subscriptions(self, subscriptions: Iterable[DataSubscription]) -> tuple[Mapping[str, object], ...]:
        return self._replay().rows_for_subscriptions(subscriptions)

    def rows_for_subscription(self, subscription: DataSubscription) -> tuple[Mapping[str, object], ...]:
        return self._replay().rows_for_subscription(subscription)

    def feed_watches(self, subscription: DataSubscription) -> tuple[MarketFeedWatchPlan, ...]:
        return self.planning.feed_watches(subscription)

    def _operations(self) -> MarketDataOperationsService:
        if self.operations is None:
            raise RuntimeError("market data service requires a dataset store for historical operations")
        return self.operations

    def _replay(self) -> MarketReplayService:
        if self.replay is None:
            raise RuntimeError("market data service requires a dataset store for replay operations")
        return self.replay


__all__ = [
    "DataSubscription",
    "HistoricalClientFactory",
    "IterableMarketEventSource",
    "MarketDataResolver",
    "MarketDataService",
    "MarketDataSubscriptionSpec",
    "MarketFeedWatchPlan",
    "MarketIngestionService",
    "MarketDataOperationsService",
    "MarketDataSpec",
    "MarketPartition",
    "MarketProjectionService",
    "MarketStreamPlanningService",
    "MarketSubscriptionService",
    "ReplayMarketDataPolicy",
    "parse_market_dataset_id",
    "replay_rows",
    "specs_from_subscription",
]
