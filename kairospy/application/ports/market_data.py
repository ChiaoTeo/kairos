from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from kairospy.application.protocol import RuntimeEnvelope

from .subscriptions import DataSubscription, MarketDataSubscriptionSpec


class MarketDataPort(Protocol):
    def events(self) -> AsyncIterator[RuntimeEnvelope]:
        ...

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        ...

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        ...

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        ...


__all__ = ["MarketDataPort"]
