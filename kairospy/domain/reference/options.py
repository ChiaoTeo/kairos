"""Stable option-contract identity exposed by the Reference domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .markets import MarketRef
from .identity import InstrumentId


@dataclass(frozen=True, slots=True)
class OptionContractRef:
    market: MarketRef
    underlying_instrument_id: InstrumentId | str
    expiry: datetime
    strike: Decimal
    right: str
    multiplier: Decimal = Decimal("100")

    def __post_init__(self) -> None:
        expiry = self.expiry if self.expiry.tzinfo is not None else self.expiry.replace(tzinfo=timezone.utc)
        object.__setattr__(self, "expiry", expiry.astimezone(timezone.utc))
        if self.strike <= 0 or self.multiplier <= 0:
            raise ValueError("option strike and multiplier must be positive")
        right = self.right.strip().lower()
        if right not in {"call", "put"}:
            raise ValueError("option right must be call or put")
        object.__setattr__(self, "right", right)


__all__ = ["OptionContractRef"]
