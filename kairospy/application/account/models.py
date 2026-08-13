from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import AccountId


class DataFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    RESYNCING = "resyncing"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Balance:
    account_id: AccountId
    asset: str
    total: Decimal
    available: Decimal
    reserved: Decimal


@dataclass(frozen=True, slots=True)
class Position:
    account_id: AccountId
    instrument: InstrumentRef
    quantity: Decimal
    average_price: Decimal | None = None
    market_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    account_id: AccountId
    equity: Decimal | None
    balances: tuple[Balance, ...]
    positions: tuple[Position, ...]
    freshness: DataFreshness
    generation: int
    event_sequence: int
