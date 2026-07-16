from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from .identity import AccountKey, AssetId, InstrumentId


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is TradeSide.BUY else -1

    @property
    def opposite(self) -> "TradeSide":
        return TradeSide.SELL if self is TradeSide.BUY else TradeSide.BUY


@dataclass(frozen=True, slots=True)
class TradeExecution:
    execution_id: UUID
    timestamp: datetime
    account: AccountKey
    instrument_id: InstrumentId
    side: TradeSide
    quantity: Decimal
    price: Decimal
    fee_asset: AssetId
    fee: Decimal
    order_id: str

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("execution timestamp must be timezone-aware")
        if self.quantity <= 0 or self.price <= 0 or self.fee < 0:
            raise ValueError("invalid execution quantity, price, or fee")


@dataclass(frozen=True, slots=True)
class FundingPayment:
    payment_id: UUID
    timestamp: datetime
    account: AccountKey
    instrument_id: InstrumentId
    settlement_asset: AssetId
    amount: Decimal
    funding_rate: Decimal
    position_notional: Decimal


@dataclass(frozen=True, slots=True)
class DividendPayment:
    payment_id: UUID
    timestamp: datetime
    account: AccountKey
    instrument_id: InstrumentId
    cash_asset: AssetId
    gross_amount: Decimal
    withholding_tax: Decimal = Decimal("0")
