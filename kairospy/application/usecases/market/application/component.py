"""Public market capability for composed business tasks.

This component aggregates market usecase capabilities for one runtime
instance.  It is not a system service and does not own connections or the
system lifecycle.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from kairospy.application.usecases.market.application.data import MarketDataApplicationService
from kairospy.application.usecases.market.application.events import MarketEventApplicationService
from kairospy.application.usecases.market.application.feed import MarketFeedApplicationService
from kairospy.application.usecases.market.application.ingestion import MarketIngestionApplicationService
from kairospy.application.usecases.market.application.projections import MarketProjectionApplicationService
from kairospy.application.usecases.market.application.query import MarketDataQueryApplicationService
from kairospy.application.usecases.market.application.replay import MarketReplayApplicationService
from kairospy.application.usecases.market.application.integration import MarketIntegrationRuntime
from kairospy.application.usecases.market.application.replay import HistoricalClientFactory, ReplayMarketDataPolicy
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.services.resolver import MarketDataResolver
from kairospy.application.usecases.market.domain.planning import MarketStreamPlanningService
from kairospy.application.usecases.market.domain.subscriptions import MarketSubscriptionService
from kairospy.application.usecases.market.domain.subscriptions import DataSubscription, DataSubscriptionGroup, MarketDataSubscriptionGroupSpec, MarketDataSubscriptionSpec
from kairospy.application.usecases.market.domain.planning import MarketFeedWatchPlan
from kairospy.application.usecases.market.application.requests import MarketDataRow, MarketOptions
from kairospy.application.usecases.market.application.resolver import ResolvedMarketData
from kairospy.application.usecases.market.protocol import MarketDataReader, MarketDataStore, MarketDataWriter, MarketHistoricalClient
from kairospy.domain.market import Bar, MarketEvent, MarketEventValue, RateObservation, MarketSubscriptionsView, MarketWindowsView, MarketWindow
from kairospy.domain.views import ViewSchema
from kairospy.application.support.messaging import Message


class MarketApplication:
    """Composed market usecase capability shared by business tasks."""

    def __init__(
        self,
        store: MarketDataStore | None = None,
        *,
        reader: MarketDataReader | None = None,
        writer: MarketDataWriter | None = None,
        resolver: MarketDataResolver | None = None,
        subscriptions: MarketSubscriptionService | None = None,
        planning: MarketStreamPlanningService | None = None,
        policy: ReplayMarketDataPolicy | None = None,
        historical_client: MarketHistoricalClient | None = None,
        historical_client_factory: HistoricalClientFactory | None = None,
        projections: MarketProjectionApplicationService | None = None,
        feed: MarketFeedApplicationService | None = None,
        integration_runtime: MarketIntegrationRuntime | None = None,
    ) -> None:
        subscription_state = subscriptions or MarketSubscriptionService()
        planning_service = planning or MarketStreamPlanningService()
        data = MarketDataApplicationService(store, resolver=resolver, integration_runtime=integration_runtime)
        self._data = data
        reader = reader or data
        if writer is None and reader is data:
            writer = data
        self._subscription_state = subscription_state
        self._planning = planning_service
        self._queries = MarketDataQueryApplicationService(reader)
        self._ingestion = MarketIngestionApplicationService(writer)
        self._replay = MarketReplayApplicationService(
            reader=reader,
            writer=writer,
            subscription_state=subscription_state,
            policy=policy,
            historical_client=historical_client,
            historical_client_factory=historical_client_factory,
        )
        self._feed = feed
        self._projections = projections or MarketProjectionApplicationService(subscriptions=self)
        self._events = MarketEventApplicationService(ingestion=self._ingestion, projection=self._projections)

    @property
    def has_historical_store(self) -> bool:
        return self._data.has_store

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        return self._queries.resolve(spec)

    def read(self, spec: MarketDataSpec, *, columns: Iterable[str] | None = None) -> list[MarketDataRow]:
        return self._queries.read(spec, columns=columns)

    def download(self, spec: MarketDataSpec, client: MarketHistoricalClient, *, mode: str = "append", options: MarketOptions | None = None) -> Path:
        return self._data.download(spec, client, mode=mode, options=options)

    def persist_historical(self, spec: MarketDataSpec, observations: Iterable[Bar | RateObservation], *, mode: str = "append") -> Path:
        return self._data.persist_historical(spec, observations, mode=mode)

    def ensure_bars(self, spec: MarketDataSpec, client: MarketHistoricalClient) -> tuple[Bar, ...]:
        return self._data.ensure_bars(spec, client)

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        return self._subscription_state.subscribe(spec)

    def subscribe_many(self, group: MarketDataSubscriptionGroupSpec) -> DataSubscriptionGroup:
        return DataSubscriptionGroup(tuple(self.subscribe(spec) for spec in group.specs))

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        self._subscription_state.unsubscribe(subscription)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return self._subscription_state.subscriptions()

    def feed_watches(self, subscription: DataSubscription) -> tuple[MarketFeedWatchPlan, ...]:
        return self._planning.feed_watches(subscription)

    def handle_event(self, message: Message) -> MarketEvent | None:
        return self._events.handle(message)

    @property
    def projection_schemas(self) -> tuple[ViewSchema, ...]:
        return self._projections.schemas

    def subscriptions_view(self) -> MarketSubscriptionsView:
        return self._projections.subscriptions_view()

    def window_views(self) -> tuple[tuple[str, str, MarketWindow[MarketEventValue]], ...]:
        return self._projections.window_views()

    def windows_view(self) -> MarketWindowsView:
        return self._projections.windows_view()

    def attach_feed(self, feed: MarketFeedApplicationService) -> None:
        if self._feed is not None and self._feed is not feed:
            raise RuntimeError("market feed is already attached to this market application")
        self._feed = feed


__all__ = ["MarketApplication"]
