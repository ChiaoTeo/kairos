from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.usecases.earn.domain import EarnPosition, EarnProduct, EarnRedeemRequest, EarnReward, EarnSubscribeRequest
from kairospy.application.usecases.earn.protocol import EarnProvider


@dataclass(frozen=True, slots=True)
class EarnApplication:
    provider: EarnProvider

    def list_products(self, *, asset: str | None = None, product_type: str | None = None) -> tuple[EarnProduct, ...]:
        return tuple(self.provider.products(asset=asset, product_type=product_type))

    def positions(self, *, asset: str | None = None) -> tuple[EarnPosition, ...]:
        return tuple(self.provider.positions(asset=asset))

    def rewards(self, *, asset: str | None = None) -> tuple[EarnReward, ...]:
        return tuple(self.provider.rewards(asset=asset))

    def subscribe(self, request: EarnSubscribeRequest) -> object:
        return self.provider.subscribe(request)

    def redeem(self, request: EarnRedeemRequest) -> object:
        return self.provider.redeem(request)


__all__ = ["EarnApplication"]
