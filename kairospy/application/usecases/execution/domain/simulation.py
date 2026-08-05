from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from kairospy.domain.order import OrderRequest, OrderSide


@dataclass(frozen=True, slots=True)
class BasisPointSlippageModel:
    basis_points: Decimal

    def __post_init__(self) -> None:
        if self.basis_points < 0:
            raise ValueError("basis_points cannot be negative")

    def price(self, side: OrderSide, price: Decimal, *, payload: Mapping[str, object]) -> Decimal:
        adjustment = self.basis_points / Decimal("10000")
        return price * (Decimal("1") + adjustment if side is OrderSide.BUY else Decimal("1") - adjustment)


@dataclass(frozen=True, slots=True)
class TradingRules:
    """Venue-independent order constraints projected from reference data."""

    status: str = "active"
    price_tick: Decimal | None = None
    amount_tick: Decimal | None = None
    min_amount: Decimal | None = None
    min_notional: Decimal | None = None

    def validate(self, order: OrderRequest, *, market_price: Decimal | None = None) -> str | None:
        if self.status.strip().lower() not in {"active", "trading"}:
            return f"market is not tradable: {self.status}"
        if self.amount_tick is not None and not _is_multiple(order.quantity, self.amount_tick):
            return f"quantity does not match amount tick: {self.amount_tick}"
        if self.min_amount is not None and order.quantity < self.min_amount:
            return f"quantity is below minimum amount: {self.min_amount}"
        if order.limit_price is not None and self.price_tick is not None and not _is_multiple(order.limit_price, self.price_tick):
            return f"limit price does not match price tick: {self.price_tick}"
        reference_price = order.limit_price or market_price
        if self.min_notional is not None and reference_price is not None and order.quantity * reference_price < self.min_notional:
            return f"order notional is below minimum: {self.min_notional}"
        return None


def _is_multiple(value: Decimal, tick: Decimal) -> bool:
    if tick <= 0:
        raise ValueError("trading rule tick must be positive")
    return value % tick == 0


__all__ = ["BasisPointSlippageModel", "TradingRules"]
