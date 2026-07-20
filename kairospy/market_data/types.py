from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from kairospy.domain.identity import AssetId, InstrumentId


class DayCount(StrEnum):
    ACT_365 = "act_365"
    ACT_360 = "act_360"


class ForwardMethod(StrEnum):
    COST_OF_CARRY = "cost_of_carry"
    PUT_CALL_PARITY = "put_call_parity"
    VENDOR = "vendor"


@dataclass(frozen=True, slots=True)
class RateNode:
    maturity_years: Decimal
    zero_rate: Decimal

    def __post_init__(self) -> None:
        if self.maturity_years < 0:
            raise ValueError("rate maturity cannot be negative")


@dataclass(frozen=True, slots=True)
class RateCurve:
    as_of: datetime
    currency: AssetId
    nodes: tuple[RateNode, ...]
    day_count: DayCount
    source: str

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("rate curve as_of must be timezone-aware")
        if not self.nodes:
            raise ValueError("rate curve requires at least one node")
        if tuple(sorted(self.nodes, key=lambda item: item.maturity_years)) != self.nodes:
            raise ValueError("rate nodes must be ordered by maturity")


@dataclass(frozen=True, slots=True)
class DividendInput:
    as_of: datetime
    underlying_id: InstrumentId
    continuous_yield: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class ForwardEstimate:
    as_of: datetime
    underlying_id: InstrumentId
    expiry: datetime
    value: Decimal
    method: ForwardMethod
    source: str

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.expiry.tzinfo is None:
            raise ValueError("forward timestamps must be timezone-aware")
        if self.expiry <= self.as_of:
            raise ValueError("forward expiry must be after as_of")
        if self.value <= 0:
            raise ValueError("forward value must be positive")


@dataclass(frozen=True, slots=True)
class OptionMarketObservation:
    instrument_id: InstrumentId
    event_time: datetime
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    source: str


@dataclass(frozen=True, slots=True)
class MarketQualityIssue:
    code: str
    severity: str
    message: str
    instrument_id: InstrumentId | None = None
