from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .domain import EarnPosition, EarnProduct, EarnRedeemRequest, EarnReward, EarnSubscribeRequest


class EarnProvider(Protocol):
    def products(self, *, asset: str | None = None, product_type: str | None = None) -> Sequence[EarnProduct]: ...
    def positions(self, *, asset: str | None = None) -> Sequence[EarnPosition]: ...
    def rewards(self, *, asset: str | None = None) -> Sequence[EarnReward]: ...
    def subscribe(self, request: EarnSubscribeRequest) -> object: ...
    def redeem(self, request: EarnRedeemRequest) -> object: ...


__all__ = ["EarnProvider"]
