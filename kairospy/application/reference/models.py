from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kairospy.domain_types import ExchangeId, InstrumentId, ListingId, MarketId


class MarketStatus(StrEnum):
    ACTIVE = "active"
    TRADING = "trading"
    INACTIVE = "inactive"
    HALTED = "halted"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class InstrumentRef:
    id: InstrumentId
    display_symbol: str

    def __post_init__(self) -> None:
        if not self.display_symbol.strip():
            raise ValueError("instrument display_symbol is required")


@dataclass(frozen=True, slots=True)
class TradingRules:
    price_increment: Decimal | None = None
    quantity_increment: Decimal | None = None
    minimum_quantity: Decimal | None = None
    minimum_notional: Decimal | None = None
    contract_multiplier: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Market:
    id: MarketId
    instrument: InstrumentRef
    listing_id: ListingId
    exchange_id: ExchangeId
    symbol: str
    market_type: str
    base_asset: str | None = None
    quote_asset: str | None = None
    status: MarketStatus = MarketStatus.UNKNOWN
    trading_rules: TradingRules = TradingRules()

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.market_type.strip():
            raise ValueError("market symbol and market_type are required")
