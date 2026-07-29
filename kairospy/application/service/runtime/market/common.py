from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.runtime.ports import DataSubscription, MarketDataSubscriptionSpec


@dataclass(frozen=True, slots=True)
class RuntimeMarketDataServiceView:
    source: str
    subscription_count: int = 0
    subscriptions: tuple[DataSubscription, ...] = ()


class MarketSubscriptionState:
    def __init__(self) -> None:
        self._subscriptions: dict[str, DataSubscription] = {}

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        subscription = DataSubscription(spec.key, spec)
        self._subscriptions[subscription.key] = subscription
        return subscription

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        key = subscription if isinstance(subscription, str) else subscription.key
        self._subscriptions.pop(key, None)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return tuple(self._subscriptions[key] for key in sorted(self._subscriptions))


__all__ = ["MarketSubscriptionState", "RuntimeMarketDataServiceView"]
