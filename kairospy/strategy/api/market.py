"""Strategy re-exports of the Market-owned event contract."""

from kairospy.contracts.market.events import (
    MarketBarCompletedEvent,
    MarketBarCurrent,
    MarketEventVariant,
    MarketFundingRateUpdatedEvent,
    MarketGreeksCurrent,
    MarketGreeksUpdatedEvent,
    MarketObservationScope,
    MarketQuoteCurrent,
    MarketQuoteUpdatedEvent,
    MarketTradeEventPayload,
    MarketTradeOccurredEvent,
)
from kairospy.primitives.decimal import DecimalValue, PriceLike, QuantityLike, RateLike
from kairospy.primitives.reference import InstrumentIdRead, MarketIdRead

Bar = MarketBarCurrent
BarEvent = MarketBarCompletedEvent
GreeksEvent = MarketGreeksUpdatedEvent
MarketEvent = MarketEventVariant
ObservationScope = MarketObservationScope
OptionGreeks = MarketGreeksCurrent
Quote = MarketQuoteCurrent
QuoteEvent = MarketQuoteUpdatedEvent
Trade = MarketTradeEventPayload
TradeEvent = MarketTradeOccurredEvent

__all__ = [
    "Bar",
    "BarEvent",
    "DecimalValue",
    "GreeksEvent",
    "InstrumentIdRead",
    "MarketEvent",
    "MarketEventVariant",
    "MarketIdRead",
    "ObservationScope",
    "OptionGreeks",
    "PriceLike",
    "QuantityLike",
    "Quote",
    "QuoteEvent",
    "RateLike",
    "Trade",
    "TradeEvent",
]
