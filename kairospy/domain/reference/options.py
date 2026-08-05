"""Stable option-contract identity exposed by the Reference domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from .markets import MarketRef
from .identity import InstrumentId

OptionRight = Literal["call", "put"]


@dataclass(frozen=True, slots=True)
class OptionContractRef:
    market: MarketRef
    underlying_instrument_id: InstrumentId
    expiry: datetime
    strike: Decimal
    right: OptionRight
    multiplier: Decimal = Decimal("100")

    def __post_init__(self) -> None:
        expiry = self.expiry if self.expiry.tzinfo is not None else self.expiry.replace(tzinfo=timezone.utc)
        object.__setattr__(self, "expiry", expiry.astimezone(timezone.utc))
        if not isinstance(self.underlying_instrument_id, InstrumentId):
            object.__setattr__(self, "underlying_instrument_id", InstrumentId(self.underlying_instrument_id))
        if self.strike <= 0 or self.multiplier <= 0:
            raise ValueError("option strike and multiplier must be positive")
        right = self.right.strip().lower()
        if right not in {"call", "put"}:
            raise ValueError("option right must be call or put")
        object.__setattr__(self, "right", right)


__all__ = ["OptionContractRef", "OptionRight"]
