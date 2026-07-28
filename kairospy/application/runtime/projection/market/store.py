from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from kairospy.core.market import (
    Bar,
    MarketEvent,
    MarketObservation,
    MarketSubject,
    OrderBookSnapshot,
    Quote,
    RateObservation,
    TradePrint,
)
from kairospy.core.views import ViewFieldSchema, ViewSchema, ViewStore
from kairospy.application.service.domains.market import MarketSubscriptionRegistry

from ...model import RuntimeDataEnvelope
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
        if isinstance(envelope.payload, MarketEvent):
            return self.apply_market_event(envelope.payload)
        if isinstance(envelope.payload, MarketObservation):
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

    def apply_market_event(self, event: MarketEvent) -> tuple[MarketFieldSummary, ...]:
        observation = _observation_from_market_event(event)
        self.update_observation(observation)
        self.subscriptions.observe(event)
        summaries = tuple(_field_summaries_from_market_event(event))
        for summary in summaries:
            self.update_field(summary)
        self._apply_typed_market_event(event)
        return summaries

    def _apply_typed_market_event(self, event: MarketEvent) -> None:
        value = event.value
        if isinstance(value, Quote):
            self.update_quote(value)
            return
        if isinstance(value, OrderBookSnapshot):
            self.update_book(_book_summary(value))
            top_quote = _quote_from_book(value)
            if top_quote is not None:
                self.update_quote(top_quote)
            return
        if isinstance(value, Bar):
            self.update_bar(_bar_summary(value))
            return
        if isinstance(value, TradePrint):
            self.update_trade(_trade_summary(value))
            return
        if isinstance(value, RateObservation):
            self.update_rate(value, kind=event.kind)

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
                kind=item.kind,
                fields=tuple(selector.key for selector in item.spec.selectors),
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

    def publisher(self) -> "MarketViewPublisher":
        from .publisher import MarketViewPublisher

        return MarketViewPublisher(self)

class MarketState(MarketStore):
    """Compatibility facade for the market runtime store.

    New runtime code should treat this as a store and use MarketProjection /
    MarketViewPublisher for projection and view publication.
    """

    def __init__(self, subscriptions: MarketSubscriptionRegistry | None = None) -> None:
        super().__init__(subscriptions)
        from .publisher import MarketViewPublisher

        self.view_publisher = MarketViewPublisher(self)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        self.view_publisher.publish(views, as_of=as_of)

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


def _observation_from_market_event(event: MarketEvent) -> MarketObservation:
    value = event.value
    payload = _market_object_payload(value)
    return MarketObservation(
        event.subject,
        event.kind,
        event.observed_at,
        payload,
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
        if field_value is None or name in {
            "instrument_id",
            "market_id",
            "market_key",
            "source",
            "basis",
            "derivation",
            "timeframe",
            "bids",
            "asks",
        }:
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
    return MarketBarSummary(
        market_id=bar.market_id,
        instrument_id=bar.instrument_id,
        market_key=bar.market_key,
        time=bar.time,
        timeframe=bar.timeframe,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        source=bar.source,
    )


def _trade_summary(trade: TradePrint) -> MarketTradeSummary:
    return MarketTradeSummary(
        market_id=trade.market_id,
        instrument_id=trade.instrument_id,
        market_key=trade.market_key,
        time=trade.time,
        trade_id=trade.trade_id,
        side=trade.side,
        price=trade.price,
        size=trade.size,
        cost=trade.cost,
        source=trade.source,
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


__all__ = [
    "MarketState",
    "MarketStore",
]
