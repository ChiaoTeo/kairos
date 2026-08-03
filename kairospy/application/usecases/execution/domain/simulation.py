from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from kairospy.domain.order import OrderSide


@dataclass(frozen=True, slots=True)
class BasisPointSlippageModel:
    basis_points: Decimal

    def __post_init__(self) -> None:
        if self.basis_points < 0:
            raise ValueError("basis_points cannot be negative")

    def price(self, side: OrderSide, price: Decimal, *, payload: Mapping[str, object]) -> Decimal:
        adjustment = self.basis_points / Decimal("10000")
        return price * (Decimal("1") + adjustment if side is OrderSide.BUY else Decimal("1") - adjustment)


__all__ = ["BasisPointSlippageModel"]
