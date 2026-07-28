from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from kairospy.core.account import AccountContext
from kairospy.core.order import OrderEventKind, OrderSide, OrderType


@dataclass(frozen=True, slots=True)
class ExecutionUpdate:
    observed_at: datetime
    kind: OrderEventKind
    venue_order_id: str = ""
    client_order_id: str | None = None
    context: AccountContext | None = None
    instrument_id: str | None = None
    market_id: str | None = None
    side: OrderSide | None = None
    quantity: Decimal | None = None
    order_type: OrderType | None = None
    limit_price: Decimal | None = None
    filled_quantity: Decimal | None = None
    remaining_quantity: Decimal | None = None
    fill_quantity: Decimal | None = None
    fill_price: Decimal | None = None
    settlement_currency: str | None = None
    cash_delta: Decimal | None = None
    fee_currency: str | None = None
    fee_amount: Decimal = Decimal("0")
    reason: str = ""
    source: str = ""
    metadata: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("execution update observed_at must be timezone-aware")
        if not isinstance(self.kind, OrderEventKind):
            object.__setattr__(self, "kind", OrderEventKind(self.kind))
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("execution update quantity must be positive")
        if self.fill_quantity is not None and self.fill_quantity <= 0:
            raise ValueError("execution update fill_quantity must be positive")
        if self.fill_price is not None and self.fill_price <= 0:
            raise ValueError("execution update fill_price must be positive")
        if self.fee_amount < 0:
            raise ValueError("execution update fee_amount cannot be negative")
        if self.fee_amount and not self.fee_currency:
            raise ValueError("execution update fee_currency is required when fee_amount is positive")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


__all__ = ["ExecutionUpdate"]
