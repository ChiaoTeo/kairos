"""Historical market-data capability used by system replay scenarios."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from kairospy.application.usecases.market.application.data import DataSubscription, MarketDataSpec, MarketDataSubscriptionSpec, MarketPartition, ResolvedMarketData
from kairospy.application.usecases.market.protocol import MarketDataReader, MarketDataWriter, MarketSubscriptionState
from kairospy.application.usecases.market.application.resolver import MarketDataResolver
from kairospy.application.usecases.market.services.operations import MarketDataOperationsService
from kairospy.application.usecases.market.services.replay import HistoricalClientFactory, MarketReplayService, ReplayMarketDataPolicy
from kairospy.application.usecases.market.services.replay import replay_rows, specs_from_subscription


class MarketReplayApplicationService:
    """Public application capability; it does not own a runtime event loop."""

    def __init__(
        self,
        store: object | None = None,
        *,
        reader: MarketDataReader | None = None,
        writer: MarketDataWriter | None = None,
        subscription_state: MarketSubscriptionState | None = None,
        resolver: MarketDataResolver | None = None,
        policy: ReplayMarketDataPolicy | None = None,
        historical_client: object | None = None,
        historical_client_factory: HistoricalClientFactory | None = None,
    ) -> None:
        operations = None
        if reader is None:
            if store is None:
                raise ValueError("market replay requires a reader or a dataset store")
            operations = MarketDataOperationsService(store, resolver=resolver)
            reader = operations
        self._reader = reader
        self._writer = writer or operations
        self._subscriptions = subscription_state
        self._replay = MarketReplayService(
            reader,
            writer=self._writer,
            policy=policy,
            historical_client=historical_client,
            historical_client_factory=historical_client_factory,
        )

    @property
    def reader(self) -> MarketDataReader:
        return self._reader

    @property
    def writer(self) -> MarketDataWriter | None:
        return self._writer

    def set_historical_client_factory(self, factory: HistoricalClientFactory | None) -> None:
        self._replay.set_historical_client_factory(factory)

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        return self._reader.resolve(spec)

    def read(self, spec: MarketDataSpec) -> list[dict[str, object]]:
        return self._reader.read(spec)

    def download(
        self,
        spec: MarketDataSpec,
        client: object,
        *,
        mode: str = "append",
        options: Mapping[str, object] | None = None,
    ) -> object:
        if self._writer is None:
            raise RuntimeError("market replay requires a data writer for download")
        return self._writer.download(spec, client, mode=mode, options=options)

    def partition_for(self, resolved: ResolvedMarketData) -> MarketPartition:
        return self._reader.partition_for(resolved)

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        if self._subscriptions is None:
            raise RuntimeError("market replay has no subscription state")
        return self._subscriptions.subscribe(spec)

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        if self._subscriptions is None:
            raise RuntimeError("market replay has no subscription state")
        self._subscriptions.unsubscribe(subscription)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        if self._subscriptions is None:
            return ()
        return self._subscriptions.subscriptions()

    def rows_for_subscriptions(self, subscriptions: Iterable[DataSubscription]) -> tuple[Mapping[str, object], ...]:
        return self._replay.rows_for_subscriptions(subscriptions)


__all__ = [
    "HistoricalClientFactory",
    "MarketReplayApplicationService",
    "ReplayMarketDataPolicy",
    "replay_rows",
    "specs_from_subscription",
]
