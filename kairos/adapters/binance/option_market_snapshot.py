from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from time import time

from kairos.domain.identity import InstrumentId


@dataclass(frozen=True, slots=True)
class OptionMarketSnapshot:
    instrument_id: InstrumentId
    bid: Decimal | None
    ask: Decimal | None
    mark_price: Decimal | None
    index_price: Decimal | None
    implied_volatility: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    event_time: datetime


def parse_option_market_snapshot(row: dict, instrument_lookup: dict[str, InstrumentId]) -> OptionMarketSnapshot:
    symbol = row.get("symbol") or row.get("s")
    if symbol not in instrument_lookup:
        raise LookupError(f"unknown option symbol: {symbol}")
    timestamp_ms = row.get("eventTime") or row.get("E") or int(time() * 1000)
    return OptionMarketSnapshot(
        instrument_lookup[symbol], _decimal(row.get("bidPrice") or row.get("b")),
        _decimal(row.get("askPrice") or row.get("a")), _decimal(row.get("markPrice") or row.get("mp")),
        _decimal(row.get("indexPrice") or row.get("bo")), _decimal(row.get("volatility") or row.get("vo")),
        _decimal(row.get("delta") or row.get("d")), _decimal(row.get("gamma") or row.get("g")),
        _decimal(row.get("theta") or row.get("t")), _decimal(row.get("vega") or row.get("v")),
        datetime.fromtimestamp(int(timestamp_ms) / 1000, timezone.utc),
    )


def _decimal(value):
    return Decimal(str(value)) if value not in (None, "") else None
