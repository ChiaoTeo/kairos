from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar


class Timeframe(StrEnum):
    MIN_1 = "1m"
    MIN_5 = "5m"
    MIN_15 = "15m"
    HOUR_1 = "1h"
    DAY_1 = "1d"


@dataclass(frozen=True, slots=True)
class MarketData:
    """One strategy-facing Market observation selector."""

    QUOTE: ClassVar[MarketData]
    TRADE: ClassVar[MarketData]
    ORDER_BOOK: ClassVar[MarketData]
    GREEKS: ClassVar[MarketData]

    selector: str

    def __post_init__(self) -> None:
        if not self.selector.strip():
            raise ValueError("market data selector is required")

    @classmethod
    def bar(cls, timeframe: Timeframe | str) -> MarketData:
        value = _wire_value(timeframe)
        if not value.strip():
            raise ValueError("bar timeframe is required")
        return cls(f"bar:{value}")


MarketData.QUOTE = MarketData("quote")
MarketData.TRADE = MarketData("trade")
MarketData.ORDER_BOOK = MarketData("order_book")
MarketData.GREEKS = MarketData("option_greeks")


def _wire_value(value: object) -> str:
    enum_value = getattr(value, "value", None)
    return enum_value if isinstance(enum_value, str) else str(value)
