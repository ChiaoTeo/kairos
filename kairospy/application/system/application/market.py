from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from kairospy.application.usecases.market.services.service import MarketDataService
from kairospy.application.usecases.market.services.replay import HistoricalClientFactory, ReplayMarketDataPolicy
from kairospy.application.usecases.market.services.resolver import MarketDataResolver, ResolvedMarketData
from kairospy.application.usecases.market.services.sources import IterableMarketEventSource
from kairospy.application.usecases.market.domain.datasets import MarketPartition
from kairospy.application.usecases.market.domain.planning import MarketFeedWatchPlan, MarketStreamPlanningService
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.domain.subscriptions import DataSubscription, MarketDataSubscriptionSpec, MarketSubscriptionService


class MarketApplication:
    """System-scoped public market component backed by private services."""

    def __init__(self, store: object | None = None, *, resolver: MarketDataResolver | None = None, subscriptions: MarketSubscriptionService | None = None, planning: MarketStreamPlanningService | None = None, policy: ReplayMarketDataPolicy | None = None, historical_client: object | None = None, historical_client_factory: HistoricalClientFactory | None = None) -> None:
        self._service = MarketDataService(store, resolver=resolver, subscriptions=subscriptions, planning=planning, policy=policy, historical_client=historical_client, historical_client_factory=historical_client_factory)

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        return self._service.resolve(spec)

    def read(self, spec: MarketDataSpec, *, columns: Iterable[str] | None = None) -> list[dict[str, object]]:
        return self._service.read(spec, columns=columns)

    def download(self, spec: MarketDataSpec, client: object, *, mode: str = "append", options: Mapping[str, object] | None = None) -> Path:
        return self._service.download(spec, client, mode=mode, params=options)

    def partition_for(self, resolved: ResolvedMarketData) -> MarketPartition:
        return self._service.partition_for(resolved)

    def partition_for_spec(self, spec: MarketDataSpec) -> MarketPartition:
        return self._service.partition_for_spec(spec)

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        return self._service.subscribe(spec)

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        self._service.unsubscribe(subscription)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return self._service.subscriptions()

    def rows_for_subscriptions(self, subscriptions: Iterable[DataSubscription]) -> tuple[Mapping[str, object], ...]:
        return self._service.rows_for_subscriptions(subscriptions)

    def rows_for_subscription(self, subscription: DataSubscription) -> tuple[Mapping[str, object], ...]:
        return self._service.rows_for_subscription(subscription)

    def feed_watches(self, subscription: DataSubscription) -> tuple[MarketFeedWatchPlan, ...]:
        return self._service.feed_watches(subscription)


__all__ = ["MarketApplication"]
