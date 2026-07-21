from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from .identity import AssetId, InstrumentId


class CorporateActionType(StrEnum):
    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    SPLIT = "split"
    REVERSE_SPLIT = "reverse_split"
    MERGER = "merger"
    SPINOFF = "spinoff"
    SYMBOL_CHANGE = "symbol_change"
    DELISTING = "delisting"


@dataclass(frozen=True, slots=True)
class SplitEvent:
    action_id: UUID
    instrument_id: InstrumentId
    effective_at: datetime
    ratio: Decimal


@dataclass(frozen=True, slots=True)
class CashDividendEvent:
    action_id: UUID
    instrument_id: InstrumentId
    ex_date: datetime
    pay_date: datetime
    cash_asset: AssetId
    amount_per_share: Decimal
    withholding_rate: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class StockDividendEvent:
    action_id: UUID
    instrument_id: InstrumentId
    effective_at: datetime
    shares_per_share: Decimal


@dataclass(frozen=True, slots=True)
class InstrumentExchangeEvent:
    action_id: UUID
    action_type: CorporateActionType
    source_instrument_id: InstrumentId
    target_instrument_id: InstrumentId
    effective_at: datetime
    target_shares_per_source_share: Decimal


@dataclass(frozen=True, slots=True)
class SymbolChangeEvent:
    action_id: UUID
    instrument_id: InstrumentId
    effective_at: datetime
    new_symbol: str
    new_external_symbol: str


@dataclass(frozen=True, slots=True)
class DelistingEvent:
    action_id: UUID
    instrument_id: InstrumentId
    effective_at: datetime
    reason: str
