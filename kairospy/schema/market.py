from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Quote:
    instrument_id: str
    time: datetime
    market_id: str | None = None
    market_key: str | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    source: str = ""

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise ValueError("quote instrument_id is required")
        if self.market_id is not None and not self.market_id.strip():
            raise ValueError("quote market_id cannot be blank")
        if self.market_key is not None and not self.market_key.strip():
            raise ValueError("quote market_key cannot be blank")
        if self.time.tzinfo is None:
            raise ValueError("quote time must be timezone-aware")
        if self.bid is not None and self.bid < 0:
            raise ValueError("quote bid cannot be negative")
        if self.ask is not None and self.ask < 0:
            raise ValueError("quote ask cannot be negative")
        if self.bid_size is not None and self.bid_size < 0:
            raise ValueError("quote bid_size cannot be negative")
        if self.ask_size is not None and self.ask_size < 0:
            raise ValueError("quote ask_size cannot be negative")

    @property
    def midpoint(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / Decimal("2")


__all__ = ["Quote"]
