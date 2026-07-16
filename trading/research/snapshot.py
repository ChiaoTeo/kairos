from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from trading.domain.identity import InstrumentId
from trading.domain.instrument import InstrumentDefinition, OptionChain
from trading.domain.market_data import Greeks, Quote, Trade
from trading.domain.market_state import MarketState

from .spec import ResearchSpec

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    code: str
    message: str
    severity: str = "warning"
    instrument_id: InstrumentId | None = None


@dataclass(frozen=True, slots=True)
class InstrumentSnapshot:
    instrument_id: InstrumentId
    quote: Quote | None
    quote_time: datetime | None
    trade: Trade | None
    trade_time: datetime | None
    greeks: Greeks | None
    greeks_time: datetime | None


@dataclass(frozen=True, slots=True)
class ResearchSnapshot:
    schema_version: int
    run_id: UUID
    created_at: datetime
    spec: ResearchSpec
    underlying_id: InstrumentId
    underlying_price: Decimal
    underlying_price_time: datetime
    option_chain: OptionChain
    definitions: tuple[InstrumentDefinition, ...]
    instruments: tuple[InstrumentSnapshot, ...]
    sources: tuple[str, ...]
    quality_issues: tuple[DataQualityIssue, ...]
    snapshot_span_seconds: float
    code_version: str


def build_snapshot(
    *,
    run_id: UUID,
    spec: ResearchSpec,
    underlying: InstrumentDefinition,
    chain: OptionChain,
    selected: tuple[InstrumentDefinition, ...],
    state: MarketState,
    now: datetime | None = None,
    code_version: str = "0.1.0",
) -> ResearchSnapshot:
    created_at = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        raise ValueError("snapshot time must be timezone-aware")
    price_entry = state.underlying_prices.get(underlying.instrument_id)
    if not price_entry or price_entry[0] <= 0:
        raise ValueError("a positive underlying price is required")
    price, price_time = price_entry
    issues: list[DataQualityIssue] = []
    if price_time.tzinfo is None:
        raise ValueError("underlying price timestamp must be timezone-aware")
    if created_at - price_time > timedelta(seconds=spec.max_quote_age_seconds):
        issues.append(DataQualityIssue("stale_underlying", "underlying price is stale", "error", underlying.instrument_id))
    snapshots: list[InstrumentSnapshot] = []
    times = [price_time]
    for definition in selected:
        instrument_id = definition.instrument_id
        listing = definition.listing(chain.venue_id)
        if not listing.external_id:
            issues.append(DataQualityIssue("unqualified_contract", "contract has no broker identifier", "error", instrument_id))
        item = state.instruments.get(instrument_id)
        if item is None:
            snapshots.append(InstrumentSnapshot(instrument_id, None, None, None, None, None, None))
            issues.append(DataQualityIssue("missing_market_data", "no market data received", "error", instrument_id))
            continue
        quote = item.quote
        if quote is None:
            issues.append(DataQualityIssue("missing_quote", "quote is missing", "error", instrument_id))
        else:
            if quote.bid is not None and quote.bid < 0 or quote.ask is not None and quote.ask < 0:
                issues.append(DataQualityIssue("negative_quote", "bid or ask is negative", "error", instrument_id))
            if quote.bid is not None and quote.ask is not None and quote.bid > quote.ask:
                issues.append(DataQualityIssue("crossed_quote", "bid exceeds ask", "error", instrument_id))
        if item.greeks is None:
            issues.append(DataQualityIssue("missing_greeks", "model Greeks are missing", "warning", instrument_id))
        for label, timestamp in (("quote", item.quote_time), ("trade", item.trade_time), ("greeks", item.greeks_time)):
            if timestamp is not None:
                if timestamp.tzinfo is None:
                    raise ValueError(f"{label} timestamp must be timezone-aware")
                times.append(timestamp)
                if created_at - timestamp > timedelta(seconds=spec.max_quote_age_seconds):
                    issues.append(DataQualityIssue("stale_data", f"{label} is stale", "warning", instrument_id))
        snapshots.append(
            InstrumentSnapshot(
                instrument_id, item.quote, item.quote_time, item.trade, item.trade_time, item.greeks, item.greeks_time
            )
        )
    span = (max(times) - min(times)).total_seconds() if times else 0.0
    sources = tuple(sorted({event.source for event in state.events}))
    return ResearchSnapshot(
        SCHEMA_VERSION,
        run_id,
        created_at,
        spec,
        underlying.instrument_id,
        price,
        price_time,
        chain,
        (underlying, *selected),
        tuple(snapshots),
        sources,
        tuple(issues),
        span,
        code_version,
    )
