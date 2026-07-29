from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SimulatedEquityPoint:
    time: datetime
    equity: Decimal
    cash: Decimal
    positions: tuple[tuple[str, Decimal], ...]


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


__all__ = ["SimulatedClosedTrade", "SimulatedEquityPoint"]
