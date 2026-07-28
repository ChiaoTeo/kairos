from __future__ import annotations

from typing import Mapping

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.services import MarketDataService
from kairospy.core.market import (
    Bar,
    MarketEvent,
    MarketEventValue,
    MarketObservation,
    MarketSubject,
    OrderBookSnapshot,
    Quote,
    RateObservation,
    TradePrint,
)
from kairospy.core.views import ViewFieldSchema, ViewSchema

from .views import (
    MarketBarSummary,
    MarketBarsView,
    MarketBookSummary,
    MarketBooksView,
    MarketFieldSummary,
    MarketFieldsView,
    MarketObservationSummary,
    MarketObservationsView,
    MarketQuoteSummary,
    MarketQuotesView,
    MarketRateSummary,
    MarketRatesView,
    MarketSubscriptionSummary,
    MarketSubscriptionsView,
    MarketTradeSummary,
    MarketTradesView,
)


class MarketStore:
    schemas = (
        ViewSchema(
            "market.subscriptions",
            "system",
            fields=(
                ViewFieldSchema("total_count", "known market subscription count", "runtime state", "market service"),
                ViewFieldSchema("active_count", "active market subscription count", "runtime state", "market service"),
                ViewFieldSchema("subscriptions", "market subscription summaries", "runtime state", "market service"),
            ),
            mutability="runtime_writable",
            evidence="runtime market subscription service",
        ),
        ViewSchema(
            "market.quotes",
            "system",
            fields=(
                ViewFieldSchema("event_count", "quote update count", "runtime sequence", "market event projection"),
                ViewFieldSchema("quotes", "latest quotes by market key", "event time", "market event projection"),
            ),
            mutability="runtime_writable",
            evidence="runtime market quote projection",
        ),
        ViewSchema(
            "market.rates",
            "system",
            fields=(
                ViewFieldSchema("event_count", "rate update count", "runtime sequence", "market event projection"),
                ViewFieldSchema("rates", "latest rates by rate or market id", "event time", "market event projection"),
            ),
            mutability="runtime_writable",
            evidence="runtime market rate projection",
        ),
        ViewSchema(
            "market.books",
            "system",
            fields=(
                ViewFieldSchema("event_count", "order book update count", "runtime sequence", "market event projection"),
                ViewFieldSchema("books", "latest order books by market key", "event time", "market event projection"),
            ),
            mutability="runtime_writable",
            evidence="runtime market book projection",
        ),
        ViewSchema(
            "market.bars",
            "system",
            fields=(
                ViewFieldSchema("event_count", "bar update count", "runtime sequence", "market event projection"),
                ViewFieldSchema("bars", "latest bars by market key and timeframe", "event time", "market event projection"),
            ),
            mutability="runtime_writable",
            evidence="runtime market bar projection",
        ),
        ViewSchema(
            "market.trades",
            "system",
            fields=(
                ViewFieldSchema("event_count", "trade update count", "runtime sequence", "market event projection"),
                ViewFieldSchema("trades", "latest trades by market key", "event time", "market event projection"),
            ),
            mutability="runtime_writable",
            evidence="runtime market trade projection",
        ),
        ViewSchema(
            "market.observations",
            "system",
            fields=(
                ViewFieldSchema("event_count", "market observation count", "runtime sequence", "market event projection"),
                ViewFieldSchema("observations", "latest observations by subject and kind", "event time", "market event projection"),
            ),
            mutability="runtime_writable",
            evidence="runtime market observation projection",
        ),
        ViewSchema(
            "market.fields",
            "system",
            fields=(
                ViewFieldSchema("event_count", "market field update count", "runtime sequence", "market field projection"),
                ViewFieldSchema("fields", "latest market facts by subject and field", "event time", "market field projection"),
            ),
            mutability="runtime_writable",
            evidence="runtime market field projection",
        ),
    )

    def __init__(self, data: MarketDataService | None = None) -> None:
        self.data = data
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

    def apply_envelope(self, envelope: RuntimeEnvelope) -> None:
        if str(envelope.domain) not in {"market", "data"}:
            return
        payload = envelope.payload
        if isinstance(payload, MarketEvent):
            self.apply_market_event(payload)
            return
        if isinstance(payload, MarketObservation):
            self.update_observation(payload)
            return
        if isinstance(payload, (Quote, OrderBookSnapshot, Bar, TradePrint, RateObservation)):
            self.apply_market_event(_market_event_from_value(payload, envelope))

    def apply_market_event(self, event: MarketEvent) -> None:
        self.update_observation(_observation_from_market_event(event))
        for summary in _field_summaries_from_market_event(event):
            self.update_field(summary)
        self._apply_typed_market_event(event.value, event.kind)

    def _apply_typed_market_event(self, value: MarketEventValue, kind: str) -> None:
        if isinstance(value, Quote):
            self.update_quote(value)
        elif isinstance(value, OrderBookSnapshot):
            self.update_book(_book_summary(value))
            quote = _quote_from_book(value)
            if quote is not None:
                self.update_quote(quote)
        elif isinstance(value, Bar):
            self.update_bar(_bar_summary(value))
        elif isinstance(value, TradePrint):
            self.update_trade(_trade_summary(value))
        elif isinstance(value, RateObservation):
            self.update_rate(value, kind=kind)
        elif isinstance(value, MarketObservation):
            self.update_observation(value)

    def update_quote(self, quote: Quote) -> Quote:
        key = _quote_state_key(quote)
        previous = self._quotes.get(key)
        if previous is not None and quote.time < previous.time:
            return previous
        self._quote_event_count += 1
        self._quotes[key] = quote
        return quote

    def update_rate(self, rate: RateObservation, *, kind: str = "interest_rate") -> RateObservation:
        key = _rate_state_key(rate)
        previous = self._rates.get(key)
        if previous is not None and rate.time < previous.time:
            return previous
        self._rate_event_count += 1
        self._rates[key] = rate
        return rate

    def update_book(self, book: MarketBookSummary) -> MarketBookSummary:
        key = book.market_key or book.market_id or book.instrument_id
        previous = self._books.get(key)
        if previous is not None and book.time < previous.time:
            return previous
        self._book_event_count += 1
        self._books[key] = book
        return book

    def update_bar(self, bar: MarketBarSummary) -> MarketBarSummary:
        key = ".".join(part for part in (bar.market_key or bar.market_id or bar.instrument_id, bar.timeframe) if part)
        previous = self._bars.get(key)
        if previous is not None and bar.time < previous.time:
            return previous
        self._bar_event_count += 1
        self._bars[key] = bar
        return bar

    def update_trade(self, trade: MarketTradeSummary) -> MarketTradeSummary:
        key = trade.market_key or trade.market_id or trade.instrument_id
        previous = self._trades.get(key)
        if previous is not None and trade.time < previous.time:
            return previous
        self._trade_event_count += 1
        self._trades[key] = trade
        return trade

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

    def update_field(self, summary: MarketFieldSummary) -> MarketFieldSummary:
        key = _field_state_key(summary)
        previous = self._fields.get(key)
        if previous is not None and summary.observed_at < previous.observed_at:
            return previous
        self._field_event_count += 1
        self._fields[key] = summary
        return summary

    def subscriptions_view(self) -> MarketSubscriptionsView:
        subscriptions = ()
        if self.data is not None:
            subscriptions = tuple(
                MarketSubscriptionSummary(
                    key=item.key,
                    subject_type="market",
                    subject_id=item.spec.market.market_key,
                    kind="data",
                    fields=tuple(selector.key for selector in item.spec.selectors),
                    status="active",
                    provider=item.spec.market.venue,
                    stream=item.spec.market.source_symbol,
                )
                for item in self.data.subscriptions()
            )
        return MarketSubscriptionsView(
            total_count=len(subscriptions),
            active_count=sum(1 for item in subscriptions if item.status == "active"),
            subscriptions=subscriptions,
        )

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
        return MarketBooksView(self._book_event_count, tuple(self._books[key] for key in sorted(self._books)))

    def bars_view(self) -> MarketBarsView:
        return MarketBarsView(self._bar_event_count, tuple(self._bars[key] for key in sorted(self._bars)))

    def trades_view(self) -> MarketTradesView:
        return MarketTradesView(self._trade_event_count, tuple(self._trades[key] for key in sorted(self._trades)))

    def observations_view(self) -> MarketObservationsView:
        return MarketObservationsView(self._observation_event_count, tuple(self._observations[key] for key in sorted(self._observations)))

    def fields_view(self) -> MarketFieldsView:
        return MarketFieldsView(self._field_event_count, tuple(self._fields[key] for key in sorted(self._fields)))


def _market_event_from_value(value: MarketEventValue, envelope: RuntimeEnvelope) -> MarketEvent:
    subject = getattr(value, "subject", None)
    if not isinstance(subject, MarketSubject):
        subject = MarketSubject("instrument", getattr(value, "instrument_id", getattr(value, "rate_id", "unknown")))
    observed_at = getattr(value, "time", getattr(value, "observed_at", envelope.time))
    source = getattr(value, "source", "")
    return MarketEvent(subject, observed_at, value, available_at=envelope.time, source=source, sequence=envelope.sequence)


def _observation_from_market_event(event: MarketEvent) -> MarketObservation:
    return MarketObservation(
        event.subject,
        event.kind,
        event.observed_at,
        _market_object_payload(event.value),
        available_at=event.available_at or event.observed_at,
        source=event.source,
        sequence=event.sequence,
    )


def _field_summaries_from_market_event(event: MarketEvent) -> tuple[MarketFieldSummary, ...]:
    value = event.value
    market_id = getattr(value, "market_id", None)
    market_key = getattr(value, "market_key", None)
    interval = getattr(value, "timeframe", None)
    summaries: list[MarketFieldSummary] = []
    for name, field_value in _market_object_payload(value).items():
        if field_value is None or name in {"instrument_id", "market_id", "market_key", "source", "basis", "derivation", "timeframe", "bids", "asks"}:
            continue
        summaries.append(
            MarketFieldSummary(
                event.subject.subject_type,
                event.subject.subject_id,
                f"{type(value).__name__}.{name}",
                event.observed_at,
                field_value,
                interval=interval,
                source=event.source,
                market_id=market_id,
                market_key=market_key,
            )
        )
    return tuple(summaries)


def _market_object_payload(value: object) -> dict[str, object]:
    if isinstance(value, Quote):
        return {
            "instrument_id": value.instrument_id,
            "market_id": value.market_id,
            "market_key": value.market_key,
            "bid": value.bid,
            "ask": value.ask,
            "bid_size": value.bid_size,
            "ask_size": value.ask_size,
            "source": value.source,
            "basis": value.basis,
            "derivation": value.derivation,
        }
    if isinstance(value, OrderBookSnapshot):
        return {
            "instrument_id": value.instrument_id,
            "market_id": value.market_id,
            "market_key": value.market_key,
            "bid1": None if value.bid1 is None else value.bid1.price,
            "ask1": None if value.ask1 is None else value.ask1.price,
            "bid_depth": len(value.bids),
            "ask_depth": len(value.asks),
            "bids": tuple((level.price, level.size) for level in value.bids),
            "asks": tuple((level.price, level.size) for level in value.asks),
            "source": value.source,
            "basis": value.basis,
            "derivation": value.derivation,
        }
    if isinstance(value, Bar):
        return {
            "instrument_id": value.instrument_id,
            "market_id": value.market_id,
            "market_key": value.market_key,
            "timeframe": value.timeframe,
            "open": value.open,
            "high": value.high,
            "low": value.low,
            "close": value.close,
            "volume": value.volume,
            "source": value.source,
            "basis": value.basis,
            "derivation": value.derivation,
        }
    if isinstance(value, TradePrint):
        return {
            "instrument_id": value.instrument_id,
            "market_id": value.market_id,
            "market_key": value.market_key,
            "trade_id": value.trade_id,
            "side": value.side,
            "price": value.price,
            "size": value.size,
            "cost": value.cost,
            "source": value.source,
            "basis": value.basis,
            "derivation": value.derivation,
        }
    if isinstance(value, RateObservation):
        return {
            "rate_id": value.rate_id,
            "market_id": value.market_id,
            "rate": value.rate,
            "tenor": value.tenor,
            "basis": value.basis,
            "source": value.source,
            "derivation": value.derivation,
        }
    if isinstance(value, MarketObservation):
        return dict(value.payload)
    return {}


def _book_summary(book: OrderBookSnapshot) -> MarketBookSummary:
    bids = tuple((level.price, level.size) for level in book.bids)
    asks = tuple((level.price, level.size) for level in book.asks)
    return MarketBookSummary(
        market_id=book.market_id,
        instrument_id=book.instrument_id,
        market_key=book.market_key,
        time=book.time,
        bid1=None if not bids else bids[0][0],
        ask1=None if not asks else asks[0][0],
        bid_depth=len(bids),
        ask_depth=len(asks),
        bids=bids,
        asks=asks,
        nonce=book.nonce,
        source=book.source,
    )


def _quote_from_book(book: OrderBookSnapshot) -> Quote | None:
    bid = book.bid1
    ask = book.ask1
    if bid is None and ask is None:
        return None
    return Quote(
        instrument_id=book.instrument_id,
        market_id=book.market_id,
        market_key=book.market_key,
        time=book.time,
        bid=None if bid is None else bid.price,
        ask=None if ask is None else ask.price,
        bid_size=None if bid is None else bid.size,
        ask_size=None if ask is None else ask.size,
        source=book.source,
        basis="orderbook_top",
        derivation="derived",
    )


def _bar_summary(bar: Bar) -> MarketBarSummary:
    return MarketBarSummary(bar.market_id, bar.instrument_id, bar.market_key, bar.time, bar.timeframe, bar.open, bar.high, bar.low, bar.close, bar.volume, bar.source)


def _trade_summary(trade: TradePrint) -> MarketTradeSummary:
    return MarketTradeSummary(trade.market_id, trade.instrument_id, trade.market_key, trade.time, trade.trade_id, trade.side, trade.price, trade.size, trade.cost, trade.source)


def _quote_state_key(quote: Quote) -> str:
    return quote.market_key or quote.market_id or quote.instrument_id


def _rate_state_key(rate: RateObservation) -> str:
    return rate.market_id or rate.rate_id


def _observation_state_key(observation: MarketObservation) -> str:
    return f"{observation.subject.subject_type}.{observation.subject.subject_id}.{observation.kind}"


def _field_state_key(summary: MarketFieldSummary) -> str:
    parts = (summary.subject_type, summary.subject_id, summary.field, summary.interval or "")
    return ".".join(_key_part(part) for part in parts)


def _key_part(value: object) -> str:
    return "".join(character if character.isalnum() else "_" for character in str(value).lower()).strip("_")


__all__ = ["MarketStore"]
