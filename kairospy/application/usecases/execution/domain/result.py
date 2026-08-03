from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.domain.order import OrderSide


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    order_id: str
    intent_id: str
    instrument_id: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    occurred_at: datetime

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price


@dataclass(frozen=True, slots=True)
class SimulatedClosedTrade:
    instrument_id: str
    opened_at: datetime
    closed_at: datetime
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    fees: Decimal

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees

    @property
    def return_pct(self) -> Decimal:
        basis = self.quantity * self.entry_price
        return Decimal("0") if basis == 0 else self.net_pnl / basis


__all__ = ["SimulatedClosedTrade", "SimulatedFill"]
