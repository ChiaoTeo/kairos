from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Protocol

from kairospy.context import DataContext
from kairospy.core.market import MarketUpdate
from kairospy.core.market import (
    FIELD_BAR_CLOSE,
    FIELD_BAR_HIGH,
    FIELD_BAR_LOW,
    FIELD_BAR_OPEN,
    FIELD_BAR_VOLUME,
    FIELD_BOOK_ASK1,
    FIELD_BOOK_ASK_DEPTH,
    FIELD_BOOK_BID1,
    FIELD_BOOK_BID_DEPTH,
    FIELD_FUNDING_RATE,
    FIELD_INTEREST_RATE,
    FIELD_QUOTE_ASK,
    FIELD_QUOTE_ASK_SIZE,
    FIELD_QUOTE_BID,
    FIELD_QUOTE_BID_SIZE,
    FIELD_QUOTE_MIDPOINT,
    FIELD_TRADE_COST,
    FIELD_TRADE_PRICE,
    FIELD_TRADE_SIDE,
    FIELD_TRADE_SIZE,
    MarketObservation,
    MarketSubject,
    MarketSubscription,
    MarketSubscriptionRegistry,
    Quote,
    RateObservation,
)
from kairospy.core.reference import MarketRef, MarketResolver
from kairospy.core.views import ViewFieldSchema, ViewSchema

from .data import RuntimeDataEnvelope


class QuoteProvider(Protocol):
    def fetch_quote(
        self,
        market: MarketRef,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object] | Quote | None:
        ...


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


@dataclass(frozen=True, slots=True)
class MarketRateSummary:
    rate_id: str
    time: datetime
    rate: Decimal
    source: str = ""
    tenor: str | None = None
    basis: str = ""
    market_id: str | None = None


@dataclass(frozen=True, slots=True)
class MarketRatesView:
    event_count: int = 0
    rates: tuple[MarketRateSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketBookSummary:
    market_id: str | None
    instrument_id: str
    market_key: str | None
    time: datetime
    bid1: Decimal | None = None
    ask1: Decimal | None = None
    bid_depth: int = 0
    ask_depth: int = 0
    bids: tuple[tuple[Decimal, Decimal], ...] = ()
    asks: tuple[tuple[Decimal, Decimal], ...] = ()
    nonce: object | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class MarketBooksView:
    event_count: int = 0
    books: tuple[MarketBookSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketBarSummary:
    market_id: str | None
    instrument_id: str
    market_key: str | None
    time: datetime
    timeframe: str | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Decimal | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class MarketBarsView:
    event_count: int = 0
    bars: tuple[MarketBarSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketTradeSummary:
    market_id: str | None
    instrument_id: str
    market_key: str | None
    time: datetime
    trade_id: str | None = None
    side: str | None = None
    price: Decimal | None = None
    size: Decimal | None = None
    cost: Decimal | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class MarketTradesView:
    event_count: int = 0
    trades: tuple[MarketTradeSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketSubscriptionSummary:
    key: str
    subject_type: str
    subject_id: str
    kind: str
    status: str
    fields: tuple[str, ...] = ()
    requested_by: str = "strategy"
    requested_at: datetime | None = None
    provider: str = ""
    stream: str = ""
    last_event_time: datetime | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class MarketSubscriptionsView:
    total_count: int = 0
    active_count: int = 0
    subscriptions: tuple[MarketSubscriptionSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketFieldSummary:
    subject_type: str
    subject_id: str
    field: str
    observed_at: datetime
    value: object
    interval: str | None = None
    source: str = ""
    market_id: str | None = None
    market_key: str | None = None


@dataclass(frozen=True, slots=True)
class MarketFieldsView:
    event_count: int = 0
    fields: tuple[MarketFieldSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketObservationSummary:
    subject_type: str
    subject_id: str
    kind: str
    observed_at: datetime
    available_at: datetime | None = None
    source: str = ""
    sequence: int | None = None
    payload: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class MarketObservationsView:
    event_count: int = 0
    observations: tuple[MarketObservationSummary, ...] = ()


class MarketState:
    key = "market.quotes"
    quotes_schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("event_count", "quote update count", "runtime sequence", "market event projection"),
            ViewFieldSchema("quotes", "latest quotes by market key", "event time", "market event projection"),
        ),
        mutability="runtime_writable",
        evidence="runtime market quote projection",
    )
    rates_schema = ViewSchema(
        "market.rates",
        "system",
        fields=(
            ViewFieldSchema("event_count", "rate update count", "runtime sequence", "market event projection"),
            ViewFieldSchema("rates", "latest rates by rate or market id", "event time", "market event projection"),
        ),
        mutability="runtime_writable",
        evidence="runtime market rate projection",
    )
    books_schema = ViewSchema(
        "market.books",
        "system",
        fields=(
            ViewFieldSchema("event_count", "order book update count", "runtime sequence", "market event projection"),
            ViewFieldSchema("books", "latest order books by market key", "event time", "market event projection"),
        ),
        mutability="runtime_writable",
        evidence="runtime market book projection",
    )
    bars_schema = ViewSchema(
        "market.bars",
        "system",
        fields=(
            ViewFieldSchema("event_count", "bar update count", "runtime sequence", "market event projection"),
            ViewFieldSchema("bars", "latest bars by market key and timeframe", "event time", "market event projection"),
        ),
        mutability="runtime_writable",
        evidence="runtime market bar projection",
    )
    trades_schema = ViewSchema(
        "market.trades",
        "system",
        fields=(
            ViewFieldSchema("event_count", "trade update count", "runtime sequence", "market event projection"),
            ViewFieldSchema("trades", "latest trades by market key", "event time", "market event projection"),
        ),
        mutability="runtime_writable",
        evidence="runtime market trade projection",
    )
    subscriptions_schema = ViewSchema(
        "market.subscriptions",
        "system",
        fields=(
            ViewFieldSchema("total_count", "known market subscription count", "runtime state", "subscription registry"),
            ViewFieldSchema("active_count", "active market subscription count", "runtime state", "subscription registry"),
            ViewFieldSchema("subscriptions", "market subscription summaries", "runtime state", "subscription registry"),
        ),
        mutability="runtime_writable",
        evidence="runtime market subscription registry",
    )
    observations_schema = ViewSchema(
        "market.observations",
        "system",
        fields=(
            ViewFieldSchema("event_count", "market observation count", "runtime sequence", "market event projection"),
            ViewFieldSchema("observations", "latest observations by subject and kind", "event time", "market event projection"),
        ),
        mutability="runtime_writable",
        evidence="runtime market observation projection",
    )
    fields_schema = ViewSchema(
        "market.fields",
        "system",
        fields=(
            ViewFieldSchema("event_count", "market field update count", "runtime sequence", "market field projection"),
            ViewFieldSchema("fields", "latest market facts by subject and field", "event time", "market field projection"),
        ),
        mutability="runtime_writable",
        evidence="runtime market field projection",
    )
    schema = quotes_schema
    schemas = (
        quotes_schema,
        rates_schema,
        books_schema,
        bars_schema,
        trades_schema,
        subscriptions_schema,
        observations_schema,
        fields_schema,
    )

    def __init__(self, subscriptions: MarketSubscriptionRegistry | None = None) -> None:
        self._quote_event_count = 0
        self._rate_event_count = 0
        self._book_event_count = 0
        self._bar_event_count = 0
        self._trade_event_count = 0
        self._observation_event_count = 0
        self._field_event_count = 0
        self._quotes: dict[str, Quote] = {}
        self._rates: dict[str, RateObservation] = {}
        self._books: dict[str, MarketBookSummary] = {}
        self._bars: dict[str, MarketBarSummary] = {}
        self._trades: dict[str, MarketTradeSummary] = {}
        self._observations: dict[str, MarketObservationSummary] = {}
        self._fields: dict[str, MarketFieldSummary] = {}
        self.subscriptions = subscriptions or MarketSubscriptionRegistry()

    def update_quote(self, quote: Quote) -> Quote:
        key = _quote_state_key(quote)
        previous = self._quotes.get(key)
        if previous is not None and quote.time < previous.time:
            return previous
        self._quote_event_count += 1
        self._quotes[key] = quote
        self.subscriptions.observe(_quote_observation(quote))
        return quote

    def update_rate(self, rate: RateObservation, *, kind: str = "interest_rate") -> RateObservation:
        key = _rate_state_key(rate)
        previous = self._rates.get(key)
        if previous is not None and rate.time < previous.time:
            return previous
        self._rate_event_count += 1
        self._rates[key] = rate
        self.subscriptions.observe(rate.to_observation(kind=kind))
        return rate

    def update_book(self, book: MarketBookSummary) -> MarketBookSummary:
        key = book.market_key or book.market_id or book.instrument_id
        previous = self._books.get(key)
        if previous is not None and book.time < previous.time:
            return previous
        self._book_event_count += 1
        self._books[key] = book
        self.subscriptions.observe(_summary_observation(book, "orderbook"))
        return book

    def update_bar(self, bar: MarketBarSummary) -> MarketBarSummary:
        key = ".".join(part for part in (bar.market_key or bar.market_id or bar.instrument_id, bar.timeframe) if part)
        previous = self._bars.get(key)
        if previous is not None and bar.time < previous.time:
            return previous
        self._bar_event_count += 1
        self._bars[key] = bar
        self.subscriptions.observe(_summary_observation(bar, "bar"))
        return bar

    def update_trade(self, trade: MarketTradeSummary) -> MarketTradeSummary:
        key = trade.market_key or trade.market_id or trade.instrument_id
        previous = self._trades.get(key)
        if previous is not None and trade.time < previous.time:
            return previous
        self._trade_event_count += 1
        self._trades[key] = trade
        self.subscriptions.observe(_summary_observation(trade, "trade"))
        return trade

    def latest_quote(self, key: str) -> Quote | None:
        return self._quotes.get(key)

    def latest_rate(self, rate_id: str) -> RateObservation | None:
        return self._rates.get(str(rate_id))

    def latest_book(self, key: str) -> MarketBookSummary | None:
        return self._books.get(key)

    def latest_bar(self, key: str, *, timeframe: str | None = None) -> MarketBarSummary | None:
        state_key = ".".join(part for part in (key, timeframe) if part)
        return self._bars.get(state_key)

    def latest_trade(self, key: str) -> MarketTradeSummary | None:
        return self._trades.get(key)

    def apply_envelope(
        self,
        envelope: RuntimeDataEnvelope,
    ) -> Quote | RateObservation | MarketBookSummary | MarketBarSummary | MarketTradeSummary | tuple[MarketFieldSummary, ...] | MarketObservationSummary | None:
        if envelope.domain != "market":
            return None
        if isinstance(envelope.payload, MarketUpdate):
            return self.apply_market_update(envelope.payload)
        if isinstance(envelope.payload, MarketObservation):
            self.subscriptions.observe(envelope.payload)
            return self.update_observation(envelope.payload)
        return None

    def update_observation(self, observation: MarketObservation) -> MarketObservationSummary:
        key = _observation_state_key(observation)
        previous = self._observations.get(key)
        if previous is not None and observation.observed_at < previous.observed_at:
            return previous
        self._observation_event_count += 1
        summary = MarketObservationSummary(
            subject_type=observation.subject.subject_type,
            subject_id=observation.subject.subject_id,
            kind=observation.kind,
            observed_at=observation.observed_at,
            available_at=observation.available_at,
            source=observation.source,
            sequence=observation.sequence,
            payload=observation.payload,
        )
        self._observations[key] = summary
        return summary

    def apply_market_update(self, update: MarketUpdate) -> tuple[MarketFieldSummary, ...]:
        observation = _observation_from_market_update(update)
        self.update_observation(observation)
        self.subscriptions.observe(observation)
        summaries = tuple(_field_summary_from_market_update(update, field, value) for field, value in update.fields.items())
        for summary in summaries:
            self.update_field(summary)
        self._apply_typed_market_update(update)
        return summaries

    def _apply_typed_market_update(self, update: MarketUpdate) -> None:
        if update.subject_type not in {"instrument", "market", "rate"}:
            return
        if update.kind in {"quote", "ticker", "orderbook"}:
            bid = _first(update.fields, FIELD_QUOTE_BID, FIELD_BOOK_BID1)
            ask = _first(update.fields, FIELD_QUOTE_ASK, FIELD_BOOK_ASK1)
            bid_size = _first(update.fields, FIELD_QUOTE_BID_SIZE)
            ask_size = _first(update.fields, FIELD_QUOTE_ASK_SIZE)
            if update.subject_type == "instrument" and any(value is not None for value in (bid, ask, bid_size, ask_size)):
                self.update_quote(
                    Quote(
                        instrument_id=update.subject_id,
                        time=update.observed_at,
                        market_id=update.market_id,
                        market_key=update.market_key,
                        bid=_optional_decimal(bid),
                        ask=_optional_decimal(ask),
                        bid_size=_optional_decimal(bid_size),
                        ask_size=_optional_decimal(ask_size),
                        source=update.source,
                    )
                )
            if update.kind == "orderbook" and update.subject_type == "instrument":
                bids = _book_levels(bid, bid_size)
                asks = _book_levels(ask, ask_size)
                self.update_book(
                    MarketBookSummary(
                        market_id=update.market_id,
                        instrument_id=update.subject_id,
                        market_key=update.market_key,
                        time=update.observed_at,
                        bid1=None if not bids else bids[0][0],
                        ask1=None if not asks else asks[0][0],
                        bid_depth=len(bids),
                        ask_depth=len(asks),
                        bids=bids,
                        asks=asks,
                        nonce=update.metadata.get("nonce"),
                        source=update.source,
                    )
                )
        if update.kind in {"bar", "ohlcv"} and update.subject_type == "instrument":
            self.update_bar(
                MarketBarSummary(
                    market_id=update.market_id,
                    instrument_id=update.subject_id,
                    market_key=update.market_key,
                    time=update.observed_at,
                    timeframe=update.interval,
                    open=_optional_decimal(update.fields.get(FIELD_BAR_OPEN)),
                    high=_optional_decimal(update.fields.get(FIELD_BAR_HIGH)),
                    low=_optional_decimal(update.fields.get(FIELD_BAR_LOW)),
                    close=_optional_decimal(update.fields.get(FIELD_BAR_CLOSE)),
                    volume=_optional_decimal(update.fields.get(FIELD_BAR_VOLUME)),
                    source=update.source,
                )
            )
        if update.kind == "trade" and update.subject_type == "instrument":
            self.update_trade(
                MarketTradeSummary(
                    market_id=update.market_id,
                    instrument_id=update.subject_id,
                    market_key=update.market_key,
                    time=update.observed_at,
                    trade_id=None if update.metadata.get("id") is None else str(update.metadata["id"]),
                    side=None if update.fields.get(FIELD_TRADE_SIDE) is None else str(update.fields[FIELD_TRADE_SIDE]),
                    price=_optional_decimal(update.fields.get(FIELD_TRADE_PRICE)),
                    size=_optional_decimal(update.fields.get(FIELD_TRADE_SIZE)),
                    cost=_optional_decimal(update.fields.get(FIELD_TRADE_COST)),
                    source=update.source,
                )
            )
        rate_value = update.fields.get(FIELD_FUNDING_RATE) or update.fields.get(FIELD_INTEREST_RATE)
        if rate_value is not None:
            self.update_rate(
                RateObservation(
                    rate_id=update.market_id or update.subject_id,
                    time=update.observed_at,
                    rate=Decimal(str(rate_value)),
                    source=update.source,
                    basis="" if update.metadata.get("basis") is None else str(update.metadata["basis"]),
                    market_id=update.market_id,
                ),
                kind=update.kind if update.kind in {"funding_rate", "interest_rate"} else "interest_rate",
            )

    def update_field(self, summary: MarketFieldSummary) -> MarketFieldSummary:
        key = _field_state_key(summary)
        previous = self._fields.get(key)
        if previous is not None and summary.observed_at < previous.observed_at:
            return previous
        self._field_event_count += 1
        self._fields[key] = summary
        return summary

    def view(self) -> MarketQuotesView:
        return self.quotes_view()

    def quotes_view(self) -> MarketQuotesView:
        return MarketQuotesView(
            event_count=self._quote_event_count,
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

    def rates_view(self) -> MarketRatesView:
        return MarketRatesView(
            event_count=self._rate_event_count,
            rates=tuple(
                MarketRateSummary(
                    rate_id=rate.rate_id,
                    time=rate.time,
                    rate=rate.rate,
                    source=rate.source,
                    tenor=rate.tenor,
                    basis=rate.basis,
                    market_id=rate.market_id,
                )
                for rate in sorted(self._rates.values(), key=_rate_state_key)
            ),
        )

    def books_view(self) -> MarketBooksView:
        return MarketBooksView(
            event_count=self._book_event_count,
            books=tuple(self._books[key] for key in sorted(self._books)),
        )

    def bars_view(self) -> MarketBarsView:
        return MarketBarsView(
            event_count=self._bar_event_count,
            bars=tuple(self._bars[key] for key in sorted(self._bars)),
        )

    def trades_view(self) -> MarketTradesView:
        return MarketTradesView(
            event_count=self._trade_event_count,
            trades=tuple(self._trades[key] for key in sorted(self._trades)),
        )

    def subscriptions_view(self) -> MarketSubscriptionsView:
        subscriptions = tuple(
            MarketSubscriptionSummary(
                key=item.key,
                subject_type=item.spec.subject_type,
                subject_id=item.spec.subject_id,
                kind=item.spec.kind,
                fields=tuple(field.key for field in item.spec.fields),
                status=item.status,
                requested_by=item.requested_by,
                requested_at=item.requested_at,
                provider=item.provider,
                stream=item.stream,
                last_event_time=item.last_event_time,
                error=item.error,
            )
            for item in self.subscriptions.list()
        )
        return MarketSubscriptionsView(
            total_count=len(subscriptions),
            active_count=sum(1 for item in subscriptions if item.status == "active"),
            subscriptions=subscriptions,
        )

    def observations_view(self) -> MarketObservationsView:
        return MarketObservationsView(
            event_count=self._observation_event_count,
            observations=tuple(self._observations[key] for key in sorted(self._observations)),
        )

    def fields_view(self) -> MarketFieldsView:
        return MarketFieldsView(
            event_count=self._field_event_count,
            fields=tuple(self._fields[key] for key in sorted(self._fields)),
        )


@dataclass(frozen=True, slots=True)
class MarketAccess:
    resolver: MarketResolver
    state: MarketState

    def latest_quote(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> Quote | None:
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
        return self.state.latest_quote(resolved.market_key)

    def latest_rate(self, rate_id: object) -> RateObservation | None:
        return self.state.latest_rate(str(rate_id))

    def latest_book(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> MarketBookSummary | None:
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
        return self.state.latest_book(resolved.market_key)

    def latest_bar(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
        timeframe: str | None = None,
    ) -> MarketBarSummary | None:
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
        return self.state.latest_bar(resolved.market_key, timeframe=timeframe)

    def latest_trade(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> MarketTradeSummary | None:
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
        return self.state.latest_trade(resolved.market_key)

    def latest_funding(
        self,
        instrument: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> RateObservation | None:
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
        return self.state.latest_rate(resolved.market_id)


class MarketRequestService:
    def __init__(
        self,
        resolver: MarketResolver,
        state: MarketState,
        *,
        phase: str,
        quote_provider: QuoteProvider | None = None,
    ) -> None:
        self.resolver = resolver
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
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
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


def _quote_observation(quote: Quote) -> MarketObservation:
    return MarketObservation(
        MarketSubject("instrument", quote.instrument_id),
        "quote",
        quote.time,
        {
            "instrument_id": quote.instrument_id,
            "market_id": quote.market_id,
            "market_key": quote.market_key,
            "bid": quote.bid,
            "ask": quote.ask,
            "bid_size": quote.bid_size,
            "ask_size": quote.ask_size,
        },
        available_at=quote.time,
        source=quote.source,
    )


def _quote_state_key(quote: Quote) -> str:
    return quote.market_key or quote.market_id or quote.instrument_id


def _rate_state_key(rate: RateObservation) -> str:
    return rate.market_id or rate.rate_id


def _observation_state_key(observation: MarketObservation) -> str:
    return f"{observation.subject.subject_type}.{observation.subject.subject_id}.{observation.kind}"


def _field_state_key(summary: MarketFieldSummary) -> str:
    parts = (summary.subject_type, summary.subject_id, summary.field, summary.interval or "")
    return ".".join(_key_part(part) for part in parts)


def _observation_from_market_update(update: MarketUpdate) -> MarketObservation:
    payload = {
        "subject_type": update.subject_type,
        "subject_id": update.subject_id,
        "market_id": update.market_id,
        "market_key": update.market_key,
        "interval": update.interval,
        **dict(update.fields),
    }
    return MarketObservation(
        MarketSubject(update.subject_type, update.subject_id),
        update.kind,
        update.observed_at,
        payload,
        available_at=update.available_at or update.observed_at,
        source=update.source,
        sequence=update.sequence,
    )


def _field_summary_from_market_update(
    update: MarketUpdate,
    field: str,
    value: object,
) -> MarketFieldSummary:
    return MarketFieldSummary(
        update.subject_type,
        update.subject_id,
        field,
        update.observed_at,
        value,
        interval=update.interval,
        source=update.source,
        market_id=update.market_id,
        market_key=update.market_key,
    )


def _summary_observation(
    summary: MarketBookSummary | MarketBarSummary | MarketTradeSummary,
    kind: str,
) -> MarketObservation:
    return MarketObservation(
        MarketSubject("instrument", summary.instrument_id),
        kind,
        summary.time,
        {
            "instrument_id": summary.instrument_id,
            "market_id": summary.market_id,
            "market_key": summary.market_key,
        },
        available_at=summary.time,
        source=summary.source,
    )


def _key_part(value: object) -> str:
    return "".join(character if character.isalnum() else "_" for character in str(value).lower()).strip("_")


def _first(value: Mapping[str, object], *keys: str) -> object | None:
    for key in keys:
        if value.get(key) is not None:
            return value[key]
    return None


def _optional_decimal(value: object | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _levels(value: object) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    levels: list[tuple[Decimal, Decimal]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        levels.append((Decimal(str(item[0])), Decimal(str(item[1]))))
    return tuple(levels)


def _book_levels(price: object | None, size: object | None) -> tuple[tuple[Decimal, Decimal], ...]:
    if price is None or size is None:
        return ()
    return ((Decimal(str(price)), Decimal(str(size))),)


def _mapping_time(value: object | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc)
    if isinstance(value, datetime):
        event_time = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        event_time = datetime.fromisoformat(text)
    if event_time.tzinfo is None:
        raise ValueError("event time must be timezone-aware")
    return event_time


__all__ = [
    "MarketAccess",
    "MarketBarSummary",
    "MarketBarsView",
    "MarketBookSummary",
    "MarketBooksView",
    "MarketFieldSummary",
    "MarketFieldsView",
    "MarketObservationSummary",
    "MarketObservationsView",
    "MarketQuoteSummary",
    "MarketQuotesView",
    "MarketRateSummary",
    "MarketRatesView",
    "MarketRequestService",
    "MarketState",
    "MarketSubscriptionSummary",
    "MarketSubscriptionsView",
    "MarketTradeSummary",
    "MarketTradesView",
    "QuoteProvider",
    "quote_from_mapping",
]
