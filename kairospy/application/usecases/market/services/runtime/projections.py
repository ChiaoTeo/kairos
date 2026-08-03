from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.usecases.market.domain.subscriptions import DataSubscription


@dataclass(frozen=True, slots=True)
class RuntimeMarketProjectionService:
    data: object

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return tuple(self.data.subscriptions())


@dataclass(frozen=True, slots=True)
class RuntimeMarketService:
    projection: RuntimeMarketProjectionService

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return self.projection.subscriptions()


__all__ = ["RuntimeMarketProjectionService", "RuntimeMarketService"]
