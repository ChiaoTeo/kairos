from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping, Protocol

from kairospy.context import DataContext
from kairospy.reference import MarketRef
from kairospy.schema import Quote
from kairospy.strategy.views import ViewFieldSchema, ViewSchema

from .events import MarketEvent, parse_event_time


@dataclass(frozen=True, slots=True)
class MarketSubscription:
    key: str
    kind: str
    market_id: str
    instrument_id: str
    venue: str
    market: str
    source_symbol: str


class QuoteProvider(Protocol):
    def fetch_quote(
        self,
        market: MarketRef,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object] | Quote | None:
        ...


class MarketSubscriptionRegistry:
    def __init__(self) -> None:
        self._subscriptions: dict[str, MarketSubscription] = {}

    def subscribe_quote(self, market: MarketRef) -> MarketSubscription:
        subscription = MarketSubscription(
            key=_quote_subscription_key(market.market_key),
            kind="quote",
            market_id=market.market_id,
            instrument_id=market.instrument_id,
            venue=market.venue,
            market=market.market,
            source_symbol=market.source_symbol,
        )
        self._subscriptions[subscription.key] = subscription
        return subscription

    def unsubscribe(self, value: MarketSubscription | str) -> None:
        key = value.key if isinstance(value, MarketSubscription) else str(value)
        self._subscriptions.pop(key, None)

    def has_quote(self, market_key: str) -> bool:
        return _quote_subscription_key(market_key) in self._subscriptions

    def list(self) -> tuple[MarketSubscription, ...]:
        return tuple(self._subscriptions[key] for key in sorted(self._subscriptions))


@dataclass(frozen=True, slots=True)
class MarketQuoteSummary:
    market_id: str | None
    instrument_id: str
    market_key: str | None
    time: datetime
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class MarketQuotesView:
    event_count: int = 0
    quotes: tuple[MarketQuoteSummary, ...] = ()


class MarketState:
    key = "market.quotes"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("event_count", "quote update count", "runtime sequence", "market event projection"),
            ViewFieldSchema("quotes", "latest quotes by market key", "event time", "market event projection"),
        ),
        mutability="runtime_writable",
        evidence="runtime market quote projection",
    )

    def __init__(self) -> None:
        self._event_count = 0
        self._quotes: dict[str, Quote] = {}

    def update_quote(self, quote: Quote) -> Quote:
        key = _quote_state_key(quote)
        previous = self._quotes.get(key)
        if previous is not None and quote.time < previous.time:
            return previous
        self._event_count += 1
        self._quotes[key] = quote
        return quote

    def latest_quote(self, key: str) -> Quote | None:
        return self._quotes.get(key)

    def apply_event(self, event: MarketEvent) -> Quote | None:
        quote = quote_from_event(event)
        if quote is None:
            return None
        return self.update_quote(quote)

    def view(self) -> MarketQuotesView:
        return MarketQuotesView(
            event_count=self._event_count,
            quotes=tuple(
                MarketQuoteSummary(
                    market_id=quote.market_id,
                    instrument_id=quote.instrument_id,
                    market_key=quote.market_key,
                    time=quote.time,
                    bid=quote.bid,
                    ask=quote.ask,
                    bid_size=quote.bid_size,
                    ask_size=quote.ask_size,
                    source=quote.source,
                )
                for quote in sorted(self._quotes.values(), key=_quote_state_key)
            ),
        )


@dataclass(frozen=True, slots=True)
class MarketAccess:
    data: DataContext
    state: MarketState

    def latest_quote(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> Quote | None:
        resolved = self.data.markets.resolve(instrument, venue=venue, market=market)
        return self.state.latest_quote(resolved.market_key)


class MarketRequestService:
    def __init__(
        self,
        data: DataContext,
        state: MarketState,
        *,
        phase: str,
        quote_provider: QuoteProvider | None = None,
    ) -> None:
        self.data = data
        self.state = state
        self.phase = phase
        self.quote_provider = quote_provider

    def request_quote(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Quote | None:
        if self.phase != "clock":
            raise RuntimeError("market requests are only allowed during on_clock")
        if self.quote_provider is None:
            raise RuntimeError("runtime has no quote provider")
        resolved = self.data.markets.resolve(instrument, venue=venue, market=market)
        value = self.quote_provider.fetch_quote(resolved, params=params)
        if value is None:
            return None
        quote = value if isinstance(value, Quote) else quote_from_mapping(
            value,
            instrument_id=resolved.instrument_id,
            market_id=resolved.market_id,
            market_key=resolved.market_key,
            source=str(value.get("source") or resolved.venue),
        )
        return self.state.update_quote(quote)


def quote_from_event(event: MarketEvent) -> Quote | None:
    payload = event.payload
    market_key = payload.get("market_key")
    market_id = payload.get("market_id")
    instrument_id = payload.get("instrument_id")
    if instrument_id is None:
        return None
    bid = _first(payload, "bid", "bid1", "bid_price")
    ask = _first(payload, "ask", "ask1", "ask_price")
    bid_size = _first(payload, "bid_size", "bid1_size")
    ask_size = _first(payload, "ask_size", "ask1_size")
    if bid is None and ask is None and bid_size is None and ask_size is None:
        return None
    return Quote(
        instrument_id=str(instrument_id),
        time=event.time,
        market_id=None if market_id is None else str(market_id),
        market_key=None if market_key is None else str(market_key),
        bid=_optional_decimal(bid),
        ask=_optional_decimal(ask),
        bid_size=_optional_decimal(bid_size),
        ask_size=_optional_decimal(ask_size),
        source=event.stream,
    )


def quote_from_mapping(
    value: Mapping[str, object],
    *,
    instrument_id: str,
    source: str,
    market_id: str | None = None,
    market_key: str | None = None,
) -> Quote:
    time_value = value.get("time") or value.get("timestamp")
    quote_time = _mapping_time(time_value)
    return Quote(
        instrument_id=instrument_id,
        time=quote_time,
        market_id=market_id,
        market_key=market_key,
        bid=_optional_decimal(_first(value, "bid", "bid1", "bid_price")),
        ask=_optional_decimal(_first(value, "ask", "ask1", "ask_price")),
        bid_size=_optional_decimal(_first(value, "bid_size", "bid1_size")),
        ask_size=_optional_decimal(_first(value, "ask_size", "ask1_size")),
        source=source,
    )


def _quote_subscription_key(market_key: str) -> str:
    return f"market.quote.{market_key}"


def _quote_state_key(quote: Quote) -> str:
    return quote.market_key or quote.market_id or quote.instrument_id


def _first(value: Mapping[str, object], *keys: str) -> object | None:
    for key in keys:
        if value.get(key) is not None:
            return value[key]
    return None


def _optional_decimal(value: object | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _mapping_time(value: object | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc)
    return parse_event_time(value)


__all__ = [
    "MarketAccess",
    "MarketQuoteSummary",
    "MarketQuotesView",
    "MarketRequestService",
    "MarketState",
    "MarketSubscription",
    "MarketSubscriptionRegistry",
    "QuoteProvider",
    "quote_from_event",
    "quote_from_mapping",
]
