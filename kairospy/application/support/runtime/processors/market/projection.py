from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, TypeVar

from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.support.runtime.services.application import RuntimeMarketService
from kairospy.core.market import (
    Bar,
    BarWindow,
    MarketEvent,
    MarketEventValue,
    MarketSubject,
    MarketSubscriptionSummary,
    MarketSubscriptionsView,
    MarketViewKeys,
    MarketWindow,
    MarketWindowSummary,
    MarketWindowsView,
    OrderBookDelta,
    OrderBookSyncGap,
    OrderBookSynchronizer,
    OrderBookSnapshot,
    OrderBookWindow,
    OptionGreeks,
    OptionGreeksWindow,
    Quote,
    QuoteWindow,
    RateObservation,
    RateWindow,
    TradePrint,
    TradeWindow,
)
from kairospy.core.views import ViewFieldSchema, ViewSchema


TWindowItem = TypeVar("TWindowItem")


class WindowReplaceKey(Protocol[TWindowItem]):
    def __call__(self, item: TWindowItem) -> object:
        ...


class MarketProjectionState:
    schemas = (
        ViewSchema(
            MarketViewKeys.subscriptions,
            "system",
            fields=(
                ViewFieldSchema("total_count", "known market subscription count", "runtime state", "market data port"),
                ViewFieldSchema("active_count", "active market subscription count", "runtime state", "market data port"),
                ViewFieldSchema("subscriptions", "market subscription summaries", "runtime state", "market data port"),
            ),
            mutability="runtime_writable",
            evidence="runtime market subscription view state",
        ),
        ViewSchema(
            MarketViewKeys.windows,
            "system",
            fields=(
                ViewFieldSchema("total_count", "known market window count", "runtime state", "market event window state"),
                ViewFieldSchema("windows", "market window summaries", "runtime state", "market event window state"),
            ),
            mutability="runtime_writable",
            evidence="runtime market window index state",
        ),
    )

    def __init__(self, service: RuntimeMarketService | None = None, *, window_size: int = 100) -> None:
        if window_size < 1:
            raise ValueError("market projection window_size must be positive")
        self.service = service
        self.window_size = window_size
        self._quote_event_count = 0
        self._rate_event_count = 0
        self._book_event_count = 0
        self._bar_event_count = 0
        self._trade_event_count = 0
        self._option_greeks_event_count = 0
        self._quote_windows: dict[str, tuple[Quote, ...]] = {}
        self._rate_windows: dict[str, tuple[RateObservation, ...]] = {}
        self._book_windows: dict[str, tuple[OrderBookSnapshot, ...]] = {}
        self._bar_windows: dict[str, tuple[Bar, ...]] = {}
        self._trade_windows: dict[str, tuple[TradePrint, ...]] = {}
        self._option_greeks_windows: dict[str, tuple[OptionGreeks, ...]] = {}
        self._book_synchronizers: dict[str, OrderBookSynchronizer] = {}
        self._quote_counts: dict[str, int] = {}
        self._rate_counts: dict[str, int] = {}
        self._book_counts: dict[str, int] = {}
        self._bar_counts: dict[str, int] = {}
        self._trade_counts: dict[str, int] = {}
        self._option_greeks_counts: dict[str, int] = {}

    def apply_envelope(self, envelope: RuntimeEnvelope) -> None:
        if str(envelope.domain) not in {"market", "data"}:
            return
        payload = envelope.payload
        if isinstance(payload, MarketEvent):
            self.apply_market_event(payload)
            return
        if isinstance(payload, (Quote, OrderBookSnapshot, Bar, TradePrint, RateObservation, OptionGreeks)):
            self.apply_market_event(_market_event_from_value(payload, envelope))

    def apply_market_event(self, event: MarketEvent) -> None:
        self._apply_typed_market_event(event.value)

    def _apply_typed_market_event(self, value: MarketEventValue) -> None:
        if isinstance(value, Quote):
            self.update_quote(value)
        elif isinstance(value, OrderBookSnapshot):
            self.update_orderbook(value)
            quote = _quote_from_book(value)
            if quote is not None:
                self.update_quote(quote)
        elif isinstance(value, OrderBookDelta):
            book = self.update_orderbook_delta(value)
            if book is not None:
                quote = _quote_from_book(book)
                if quote is not None:
                    self.update_quote(quote)
        elif isinstance(value, Bar):
            self.update_bar(value)
        elif isinstance(value, TradePrint):
            self.update_trade(value)
        elif isinstance(value, RateObservation):
            self.update_rate(value)
        elif isinstance(value, OptionGreeks):
            self.update_option_greeks(value)

    def update_quote(self, quote: Quote) -> Quote:
        key = _market_state_key(quote)
        self._quote_windows[key] = _append_window(self._quote_windows.get(key, ()), quote, limit=self.window_size, replace_key=_quote_replace_key)
        self._quote_counts[key] = self._quote_counts.get(key, 0) + 1
        self._quote_event_count += 1
        return self._quote_windows[key][-1]

    def update_rate(self, rate: RateObservation) -> RateObservation:
        key = _rate_state_key(rate)
        self._rate_windows[key] = _append_window(self._rate_windows.get(key, ()), rate, limit=self.window_size, replace_key=_rate_replace_key)
        self._rate_counts[key] = self._rate_counts.get(key, 0) + 1
        self._rate_event_count += 1
        return self._rate_windows[key][-1]

    def update_orderbook(self, book: OrderBookSnapshot) -> OrderBookSnapshot:
        key = _market_state_key(book)
        self._book_synchronizers[key] = OrderBookSynchronizer(book)
        self._book_windows[key] = _append_window(self._book_windows.get(key, ()), book, limit=self.window_size, replace_key=_orderbook_replace_key)
        self._book_counts[key] = self._book_counts.get(key, 0) + 1
        self._book_event_count += 1
        return self._book_windows[key][-1]

    def update_orderbook_delta(self, delta: OrderBookDelta) -> OrderBookSnapshot | None:
        key = _market_state_key(delta)
        synchronizer = self._book_synchronizers.get(key)
        if synchronizer is None:
            return None
        try:
            snapshot = synchronizer.apply(delta).book
        except OrderBookSyncGap:
            return None
        self._book_windows[key] = _append_window(
            self._book_windows.get(key, ()),
            snapshot,
            limit=self.window_size,
            replace_key=_orderbook_replace_key,
        )
        self._book_counts[key] = self._book_counts.get(key, 0) + 1
        self._book_event_count += 1
        return self._book_windows[key][-1]

    def update_bar(self, bar: Bar) -> Bar:
        key = ".".join(part for part in (_market_state_key(bar), bar.timeframe) if part)
        self._bar_windows[key] = _append_window(self._bar_windows.get(key, ()), bar, limit=self.window_size, replace_key=_bar_replace_key)
        self._bar_counts[key] = self._bar_counts.get(key, 0) + 1
        self._bar_event_count += 1
        return self._bar_windows[key][-1]

    def update_trade(self, trade: TradePrint) -> TradePrint:
        key = _market_state_key(trade)
        self._trade_windows[key] = _append_window(self._trade_windows.get(key, ()), trade, limit=self.window_size, replace_key=_trade_replace_key)
        self._trade_counts[key] = self._trade_counts.get(key, 0) + 1
        self._trade_event_count += 1
        return self._trade_windows[key][-1]

    def update_option_greeks(self, greeks: OptionGreeks) -> OptionGreeks:
        key = _market_state_key(greeks)
        self._option_greeks_windows[key] = _append_window(self._option_greeks_windows.get(key, ()), greeks, limit=self.window_size, replace_key=_option_greeks_replace_key)
        self._option_greeks_counts[key] = self._option_greeks_counts.get(key, 0) + 1
        self._option_greeks_event_count += 1
        return self._option_greeks_windows[key][-1]

    def subscriptions_view(self) -> MarketSubscriptionsView:
        subscriptions = ()
        if self.service is not None:
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
                for item in self.service.subscriptions()
            )
        return MarketSubscriptionsView(
            total_count=len(subscriptions),
            active_count=sum(1 for item in subscriptions if item.status == "active"),
            subscriptions=subscriptions,
        )

    def windows_view(self) -> MarketWindowsView:
        summaries = tuple(_window_summary(key, kind, window) for key, kind, window in self.window_views())
        return MarketWindowsView(total_count=len(summaries), windows=summaries)

    def window_views(self) -> tuple[tuple[str, str, MarketWindow[object]], ...]:
        views: list[tuple[str, str, MarketWindow[object]]] = []
        views.extend(
            (MarketViewKeys.quotes(key), "quotes", _quote_window(key, self._quote_windows[key], self._quote_counts[key]))
            for key in sorted(self._quote_windows)
        )
        views.extend(
            (MarketViewKeys.rates(_rate_market_key(self._rate_windows[key][-1]), self._rate_windows[key][-1].basis or "default"), "rates", _rate_window(key, self._rate_windows[key], self._rate_counts[key]))
            for key in sorted(self._rate_windows)
        )
        views.extend(
            (MarketViewKeys.orderbooks(key), "orderbooks", _orderbook_window(key, self._book_windows[key], self._book_counts[key]))
            for key in sorted(self._book_windows)
        )
        views.extend(
            (MarketViewKeys.bars(_market_state_key(self._bar_windows[key][-1]), self._bar_windows[key][-1].timeframe), "bars", _bar_window(key, self._bar_windows[key], self._bar_counts[key]))
            for key in sorted(self._bar_windows)
        )
        views.extend(
            (MarketViewKeys.trades(key), "trades", _trade_window(key, self._trade_windows[key], self._trade_counts[key]))
            for key in sorted(self._trade_windows)
        )
        views.extend(
            (MarketViewKeys.option_greeks(key), "option_greeks", _option_greeks_window(key, self._option_greeks_windows[key], self._option_greeks_counts[key]))
            for key in sorted(self._option_greeks_windows)
        )
        return tuple(views)


def _market_event_from_value(value: MarketEventValue, envelope: RuntimeEnvelope) -> MarketEvent:
    subject = getattr(value, "subject", None)
    if not isinstance(subject, MarketSubject):
        subject = MarketSubject("instrument", getattr(value, "instrument_id", getattr(value, "rate_id", "unknown")))
    observed_at = getattr(value, "time", envelope.time)
    source = getattr(value, "source", "")
    return MarketEvent(subject, observed_at, value, available_at=envelope.time, source=source, sequence=envelope.sequence)


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


def _append_window(
    items: tuple[TWindowItem, ...],
    item: TWindowItem,
    *,
    limit: int,
    replace_key: WindowReplaceKey[TWindowItem],
) -> tuple[TWindowItem, ...]:
    incoming_key = replace_key(item)
    values = tuple(existing for existing in items if replace_key(existing) != incoming_key) + (item,)
    values = tuple(sorted(values, key=lambda value: _event_time(value)))
    return values[-limit:]


def _quote_window(key: str, items: tuple[Quote, ...], event_count: int) -> QuoteWindow:
    latest = items[-1]
    return QuoteWindow(
        subject_type="market",
        subject_id=key,
        market_id=latest.market_id,
        market_key=latest.market_key,
        instrument_id=latest.instrument_id,
        source=latest.source,
        updated_at=latest.time,
        items=items,
        event_count=event_count,
    )


def _rate_window(key: str, items: tuple[RateObservation, ...], event_count: int) -> RateWindow:
    latest = items[-1]
    market_key = _rate_market_key(latest)
    return RateWindow(
        subject_type="market" if latest.market_id is not None else "rate",
        subject_id=key,
        market_id=latest.market_id,
        market_key=market_key,
        instrument_id=latest.instrument_id,
        source=latest.source,
        updated_at=latest.time,
        items=items,
        event_count=event_count,
        basis=latest.basis,
    )


def _orderbook_window(key: str, items: tuple[OrderBookSnapshot, ...], event_count: int) -> OrderBookWindow:
    latest = items[-1]
    return OrderBookWindow(
        subject_type="market",
        subject_id=key,
        market_id=latest.market_id,
        market_key=latest.market_key,
        instrument_id=latest.instrument_id,
        source=latest.source,
        updated_at=latest.time,
        items=items,
        event_count=event_count,
    )


def _bar_window(key: str, items: tuple[Bar, ...], event_count: int) -> BarWindow:
    latest = items[-1]
    return BarWindow(
        subject_type="market",
        subject_id=key,
        market_id=latest.market_id,
        market_key=latest.market_key,
        instrument_id=latest.instrument_id,
        source=latest.source,
        updated_at=latest.time,
        items=items,
        event_count=event_count,
        timeframe=latest.timeframe,
    )


def _trade_window(key: str, items: tuple[TradePrint, ...], event_count: int) -> TradeWindow:
    latest = items[-1]
    return TradeWindow(
        subject_type="market",
        subject_id=key,
        market_id=latest.market_id,
        market_key=latest.market_key,
        instrument_id=latest.instrument_id,
        source=latest.source,
        updated_at=latest.time,
        items=items,
        event_count=event_count,
    )


def _option_greeks_window(key: str, items: tuple[OptionGreeks, ...], event_count: int) -> OptionGreeksWindow:
    latest = items[-1]
    return OptionGreeksWindow(
        subject_type="market",
        subject_id=key,
        market_id=latest.market_id,
        market_key=latest.market_key,
        instrument_id=latest.instrument_id,
        source=latest.source,
        updated_at=latest.time,
        items=items,
        event_count=event_count,
    )


def _window_summary(key: str, kind: str, window: MarketWindow[object]) -> MarketWindowSummary:
    return MarketWindowSummary(
        key=key,
        kind=kind,
        subject_type=window.subject_type,
        subject_id=window.subject_id,
        market_id=window.market_id,
        market_key=window.market_key,
        instrument_id=window.instrument_id,
        qualifier=_window_qualifier(window),
        item_count=window.size,
        event_count=window.event_count,
        updated_at=window.updated_at,
    )


def _window_qualifier(window: MarketWindow[object]) -> str:
    if isinstance(window, BarWindow):
        return window.timeframe
    if isinstance(window, RateWindow):
        return window.basis
    return ""


def _market_state_key(item: object) -> str:
    return _optional_id(getattr(item, "market_key", None)) or _optional_id(getattr(item, "market_id", None)) or str(getattr(item, "instrument_id"))


def _rate_state_key(rate: RateObservation) -> str:
    return ".".join(part for part in (_optional_id(rate.market_id) or rate.rate_id, rate.basis) if part)


def _rate_market_key(rate: RateObservation) -> str:
    return _market_key_from_id(rate.market_id) or _market_key_from_id(rate.instrument_id) or rate.rate_id


def _market_key_from_id(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text.startswith("market:"):
        return "_".join(text.removeprefix("market:").split(":"))
    if text.startswith("instrument:"):
        return "_".join(text.removeprefix("instrument:").split(":"))
    return text


def _quote_replace_key(quote: Quote) -> object:
    return quote.time, quote.basis


def _rate_replace_key(rate: RateObservation) -> object:
    return rate.time, rate.basis


def _orderbook_replace_key(book: OrderBookSnapshot) -> object:
    return book.time, book.nonce


def _bar_replace_key(bar: Bar) -> object:
    return bar.time, bar.timeframe


def _trade_replace_key(trade: TradePrint) -> object:
    return trade.trade_id or (trade.time, trade.price, trade.size, trade.side)


def _option_greeks_replace_key(greeks: OptionGreeks) -> object:
    return greeks.time, greeks.basis


def _event_time(item: object) -> datetime:
    value = getattr(item, "time", None)
    if isinstance(value, datetime):
        return value
    return datetime.min.replace(tzinfo=timezone.utc)


def _optional_id(value: object | None) -> str | None:
    return None if value is None else str(value)


__all__ = ["MarketProjectionState"]
