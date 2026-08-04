"""Market subscription capability exposed to system application code."""

from __future__ import annotations

from kairospy.application.usecases.market.application.data import (
    DataSubscription,
    DataSubscriptionGroup,
    MarketDataSubscriptionGroupSpec,
    MarketDataSubscriptionSpec,
)
from kairospy.application.usecases.market.domain.planning import MarketStreamPlanningService
from kairospy.application.usecases.market.protocol import MarketSubscriptionState


class MarketSubscriptionApplicationService:
    """Owns market subscription intent and planning semantics."""

    def __init__(self, state: MarketSubscriptionState, *, planning: MarketStreamPlanningService | None = None) -> None:
        self._state = state
        self._planning = planning or MarketStreamPlanningService()

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        return self._state.subscribe(spec)

    def subscribe_many(self, group: MarketDataSubscriptionGroupSpec) -> DataSubscriptionGroup:
        return DataSubscriptionGroup(tuple(self._state.subscribe(spec) for spec in group.specs))

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        self._state.unsubscribe(subscription)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return self._state.subscriptions()

    def feed_watches(self, subscription: DataSubscription) -> tuple[MarketFeedWatchPlan, ...]:
        return self._planning.feed_watches(subscription)

__all__ = ["MarketSubscriptionApplicationService"]
