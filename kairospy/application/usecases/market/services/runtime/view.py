from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.usecases.market.application.data import DataSubscription


@dataclass(frozen=True, slots=True)
class RuntimeMarketDataServiceView:
    source: str
    subscription_count: int = 0
    subscriptions: tuple[DataSubscription, ...] = ()


__all__ = ["RuntimeMarketDataServiceView"]
