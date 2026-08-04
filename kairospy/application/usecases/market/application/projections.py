"""Public market projection capability."""

from __future__ import annotations

from kairospy.application.usecases.market.services.projections import MarketProjectionService as _MarketProjectionService
from kairospy.application.usecases.market.protocol import MarketSubscriptionState


class MarketProjectionApplicationService:
    """Maintains market windows and publishes market view payloads."""

    def __init__(self, subscriptions: MarketSubscriptionState | None = None, *, window_size: int = 100) -> None:
        self._service = _MarketProjectionService(subscriptions=subscriptions, window_size=window_size)

    @property
    def schemas(self) -> tuple[object, ...]:
        return self._service.schemas

    def apply(self, event: object) -> None:
        self._service.apply(event)  # type: ignore[arg-type]

    def subscriptions_view(self) -> object:
        return self._service.subscriptions_view()

    def window_views(self) -> tuple[tuple[str, str, object], ...]:
        return self._service.window_views()

    def windows_view(self) -> object:
        return self._service.windows_view()


__all__ = ["MarketProjectionApplicationService"]
