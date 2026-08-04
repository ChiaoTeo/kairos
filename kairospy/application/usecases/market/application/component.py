"""Public market capability for composed business tasks.

This component aggregates market usecase capabilities for one runtime
instance.  It is not a system service and does not own connections or the
system lifecycle.
"""

from __future__ import annotations

from kairospy.application.usecases.market.application.data import MarketDataApplicationService
from kairospy.application.usecases.market.application.events import MarketEventApplicationService
from kairospy.application.usecases.market.application.feed import MarketFeedApplicationService
from kairospy.application.usecases.market.application.ingestion import MarketIngestionApplicationService
from kairospy.application.usecases.market.application.projections import MarketProjectionApplicationService
from kairospy.application.usecases.market.application.query import MarketDataQueryApplicationService
from kairospy.application.usecases.market.application.replay import MarketReplayApplicationService
from kairospy.application.usecases.market.application.subscriptions import MarketSubscriptionApplicationService
from kairospy.application.usecases.market.domain.planning import MarketStreamPlanningService
from kairospy.application.usecases.market.domain.subscriptions import MarketSubscriptionService
from kairospy.application.usecases.market.protocol import MarketDataReader, MarketDataWriter
from kairospy.application.usecases.market.application.integration import MarketIntegrationRuntime


class MarketApplication:
    """Composed market usecase capability shared by business tasks."""

    def __init__(
        self,
        store: object | None = None,
        *,
        reader: MarketDataReader | None = None,
        writer: MarketDataWriter | None = None,
        resolver: object | None = None,
        subscriptions: MarketSubscriptionService | None = None,
        planning: MarketStreamPlanningService | None = None,
        policy: object | None = None,
        historical_client: object | None = None,
        historical_client_factory: object | None = None,
        projections: MarketProjectionApplicationService | None = None,
        feed: MarketFeedApplicationService | None = None,
        integration_runtime: MarketIntegrationRuntime | None = None,
    ) -> None:
        subscription_state = subscriptions or MarketSubscriptionService()
        planning_service = planning or MarketStreamPlanningService()
        data = MarketDataApplicationService(store, resolver=resolver, integration_runtime=integration_runtime)
        reader = reader or data
        if writer is None and reader is data:
            writer = data
        self.subscriptions = MarketSubscriptionApplicationService(subscription_state, planning=planning_service)
        self.queries = MarketDataQueryApplicationService(reader)
        self.ingestion = MarketIngestionApplicationService(writer)
        self.replay = MarketReplayApplicationService(
            reader=reader,
            writer=writer,
            subscription_state=subscription_state,
            policy=policy,
            historical_client=historical_client,
            historical_client_factory=historical_client_factory,
        )
        self.feed = feed
        self.projections = projections or MarketProjectionApplicationService(subscriptions=self.subscriptions)
        self.events = MarketEventApplicationService(ingestion=self.ingestion, projection=self.projections)

    def attach_feed(self, feed: MarketFeedApplicationService) -> None:
        if self.feed is not None and self.feed is not feed:
            raise RuntimeError("market feed is already attached to this market application")
        self.feed = feed


__all__ = ["MarketApplication"]
