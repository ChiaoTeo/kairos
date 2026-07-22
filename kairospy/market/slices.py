from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.identity import InstrumentId

from .types import Greeks, Quote, Trade


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    code: str
    message: str
    severity: str = "warning"
    instrument_id: InstrumentId | None = None


MarketSliceQualityIssue = DataQualityIssue


@dataclass(frozen=True, slots=True)
class InstrumentSnapshot:
    instrument_id: InstrumentId
    quote: Quote | None
    quote_time: datetime | None
    trade: Trade | None
    trade_time: datetime | None
    greeks: Greeks | None
    greeks_time: datetime | None


class MarketInstrumentSlice(Protocol):
    instrument_id: InstrumentId
    quote: Quote | None
    quote_time: datetime | None
    trade: Trade | None
    trade_time: datetime | None
    greeks: Greeks | None
    greeks_time: datetime | None


class MarketSlice(Protocol):
    timestamp: datetime
    available_time: datetime | None
    instruments: tuple[MarketInstrumentSlice, ...]
    reference_prices: tuple[tuple[InstrumentId, Decimal], ...]
    quality_issues: tuple[object, ...]
    snapshot_span_seconds: Decimal
    sequence: int
    available_instruments: tuple[InstrumentId, ...]

    @property
    def instrument_universe(self) -> tuple[InstrumentId, ...]: ...
