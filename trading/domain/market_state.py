from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .event import GreeksUpdated, MarketEvent, OptionChainDiscovered, QuoteUpdated, TradeUpdated, UnderlyingPriceUpdated
from .identity import InstrumentId
from .instrument import OptionChain
from .market_data import FundingRate, Greeks, IndexPrice, MarkPrice, OpenInterest, Quote, Trade, TradingStatus, VolatilitySurfacePoint


@dataclass(slots=True)
class InstrumentMarketState:
    quote: Quote | None = None
    quote_time: datetime | None = None
    trade: Trade | None = None
    trade_time: datetime | None = None
    greeks: Greeks | None = None
    greeks_time: datetime | None = None


@dataclass(slots=True)
class MarketState:
    instruments: dict[InstrumentId, InstrumentMarketState] = field(default_factory=dict)
    underlying_prices: dict[InstrumentId, tuple[Decimal, datetime]] = field(default_factory=dict)
    chains: dict[InstrumentId, OptionChain] = field(default_factory=dict)
    events: list[MarketEvent] = field(default_factory=list)
    normalized: dict[tuple[InstrumentId, str], object] = field(default_factory=dict)

    def for_instrument(self, instrument_id: InstrumentId) -> InstrumentMarketState:
        return self.instruments.setdefault(instrument_id, InstrumentMarketState())


def apply_market_event(state: MarketState, event: MarketEvent) -> None:
    payload = event.payload
    if isinstance(payload, UnderlyingPriceUpdated):
        state.underlying_prices[payload.instrument_id] = (payload.price, event.event_time)
    elif isinstance(payload, QuoteUpdated):
        target = state.for_instrument(payload.quote.instrument_id)
        target.quote, target.quote_time = payload.quote, event.event_time
    elif isinstance(payload, TradeUpdated):
        target = state.for_instrument(payload.trade.instrument_id)
        target.trade, target.trade_time = payload.trade, event.event_time
    elif isinstance(payload, GreeksUpdated):
        target = state.for_instrument(payload.greeks.instrument_id)
        target.greeks, target.greeks_time = payload.greeks, event.event_time
    elif isinstance(payload, OptionChainDiscovered):
        state.chains[payload.chain.underlying_id] = payload.chain
    elif isinstance(payload, (IndexPrice, MarkPrice, FundingRate, OpenInterest, TradingStatus)):
        state.normalized[(payload.instrument_id, type(payload).__name__)] = payload
    elif isinstance(payload, VolatilitySurfacePoint):
        state.normalized[(payload.underlying_id, f"VolatilitySurfacePoint:{payload.expiry.isoformat()}:{payload.strike}")] = payload
    else:
        raise TypeError(f"unsupported market payload: {type(payload).__name__}")
    state.events.append(event)
