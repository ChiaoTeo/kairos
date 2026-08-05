"""Public market projection capability."""

from __future__ import annotations

from kairospy.application.usecases.market.services.projections import MarketProjectionService as _MarketProjectionService
from kairospy.application.usecases.market.protocol import MarketSubscriptionState
from kairospy.domain.market import MarketEvent, MarketEventValue, MarketWindow, MarketWindowsView, MarketSubscriptionsView
from kairospy.domain.views import ViewSchema


class MarketProjectionApplicationService:
    """Maintains market windows and publishes market view payloads."""

    def __init__(self, subscriptions: MarketSubscriptionState | None = None, *, window_size: int = 100) -> None:
        self._service = _MarketProjectionService(subscriptions=subscriptions, window_size=window_size)

    @property
    def schemas(self) -> tuple[ViewSchema, ...]:
        return self._service.schemas

    def apply(self, event: MarketEvent) -> None:
        self._service.apply(event)

    def subscriptions_view(self) -> MarketSubscriptionsView:
        return self._service.subscriptions_view()

    def window_views(self) -> tuple[tuple[str, str, MarketWindow[MarketEventValue]], ...]:
        return self._service.window_views()

    def windows_view(self) -> MarketWindowsView:
        return self._service.windows_view()


__all__ = ["MarketProjectionApplicationService"]
