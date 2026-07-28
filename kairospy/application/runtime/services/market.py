from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kairospy.application.runtime.protocol.lines import RuntimeEventLine

from .subscriptions import DataSubscription, MarketDataSubscriptionSpec

from .component import RuntimeComponent, RuntimeViewPublisher


class MarketDataService(RuntimeEventLine, RuntimeComponent, Protocol):
    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        ...

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        ...

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        ...


@dataclass(frozen=True, slots=True)
class MarketDataProjectionProvider:
    data: MarketDataService

    def runtime_components(self) -> tuple[RuntimeViewPublisher, ...]:
        from kairospy.application.runtime.projection.market import MarketProjection, MarketStore

        return (MarketProjection(MarketStore(data=self.data)),)


__all__ = ["MarketDataProjectionProvider", "MarketDataService"]
