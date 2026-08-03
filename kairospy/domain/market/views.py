from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Generic, Protocol, TypeVar

from kairospy.domain.reference import ExchangeId, InstrumentId, MarketId, MarketRef, MarketResolver, MarketTypeId, SourceSymbol
from kairospy.domain.views import ViewFieldSchema, ViewSchema

from .model import Bar, OptionGreeks, Quote, RateObservation, TradePrint
from .orderbook import OrderBookSnapshot


class MarketViewKeys:
    subscriptions = "market.subscriptions"
    windows = "market.windows"
    prefix = "market.window"

    @staticmethod
    def quotes(market_key: object) -> str:
        return f"{MarketViewKeys.prefix}.{_key_part(market_key)}.quotes"

    @staticmethod
    def trades(market_key: object) -> str:
        return f"{MarketViewKeys.prefix}.{_key_part(market_key)}.trades"

    @staticmethod
    def orderbooks(market_key: object) -> str:
        return f"{MarketViewKeys.prefix}.{_key_part(market_key)}.orderbooks"

    @staticmethod
    def bars(market_key: object, timeframe: object) -> str:
        return f"{MarketViewKeys.prefix}.{_key_part(market_key)}.bars.{_key_part(timeframe)}"

    @staticmethod
    def rates(market_key: object, basis: object) -> str:
        return f"{MarketViewKeys.prefix}.{_key_part(market_key)}.rates.{_key_part(basis)}"

    @staticmethod
    def option_greeks(market_key: object) -> str:
        return f"{MarketViewKeys.prefix}.{_key_part(market_key)}.option_greeks"


class MarketViewSource(Protocol):
    def get(self, key: str, default: object = None) -> object:
        ...


MarketViewSubject = MarketRef | MarketId | InstrumentId | SourceSymbol | str
MarketViewExchange = ExchangeId | str
MarketViewMarketType = MarketTypeId | str
TMarketItem = TypeVar("TMarketItem")


@dataclass(frozen=True, slots=True)
class MarketWindow(Generic[TMarketItem]):
    subject_type: str
    subject_id: str
    items: tuple[TMarketItem, ...] = ()
    event_count: int = 0
    market_id: MarketId | str | None = None
    market_key: str | None = None
    instrument_id: InstrumentId | str | None = None
    source: str = ""
    updated_at: datetime | None = None

    @property
    def latest(self) -> TMarketItem | None:
        return self.items[-1] if self.items else None

    @property
    def previous(self) -> TMarketItem | None:
        return self.items[-2] if len(self.items) > 1 else None

    @property
    def size(self) -> int:
        return len(self.items)

    @property
    def empty(self) -> bool:
        return not self.items


@dataclass(frozen=True, slots=True)
class QuoteWindow(MarketWindow[Quote]):
    pass


@dataclass(frozen=True, slots=True)
class TradeWindow(MarketWindow[TradePrint]):
    pass


@dataclass(frozen=True, slots=True)
class BarWindow(MarketWindow[Bar]):
    timeframe: str = ""


@dataclass(frozen=True, slots=True)
class RateWindow(MarketWindow[RateObservation]):
    basis: str = ""


@dataclass(frozen=True, slots=True)
class OptionGreeksWindow(MarketWindow[OptionGreeks]):
    pass


@dataclass(frozen=True, slots=True)
class OrderBookChangeSummary:
    bid_price_change: Decimal | None = None
    ask_price_change: Decimal | None = None
    bid_size_change: Decimal | None = None
    ask_size_change: Decimal | None = None
    spread_change: Decimal | None = None
    depth_change: int = 0


@dataclass(frozen=True, slots=True)
class OrderBookWindow(MarketWindow[OrderBookSnapshot]):
    @property
    def current(self) -> OrderBookSnapshot | None:
        return self.latest

    @property
    def bid(self) -> Decimal | None:
        current = self.current
        return None if current is None or current.bid1 is None else current.bid1.price

    @property
    def ask(self) -> Decimal | None:
        current = self.current
        return None if current is None or current.ask1 is None else current.ask1.price

    @property
    def spread(self) -> Decimal | None:
        bid = self.bid
        ask = self.ask
        if bid is None or ask is None:
            return None
        return ask - bid

    @property
    def change(self) -> OrderBookChangeSummary | None:
        current = self.current
        previous = self.previous
        if current is None or previous is None:
            return None
        return OrderBookChangeSummary(
            bid_price_change=_decimal_diff(
                None if current.bid1 is None else current.bid1.price,
                None if previous.bid1 is None else previous.bid1.price,
            ),
            ask_price_change=_decimal_diff(
                None if current.ask1 is None else current.ask1.price,
                None if previous.ask1 is None else previous.ask1.price,
            ),
            bid_size_change=_decimal_diff(
                None if current.bid1 is None else current.bid1.size,
                None if previous.bid1 is None else previous.bid1.size,
            ),
            ask_size_change=_decimal_diff(
                None if current.ask1 is None else current.ask1.size,
                None if previous.ask1 is None else previous.ask1.size,
            ),
            spread_change=_decimal_diff(_spread(current), _spread(previous)),
            depth_change=(len(current.bids) + len(current.asks)) - (len(previous.bids) + len(previous.asks)),
        )


@dataclass(frozen=True, slots=True)
class MarketWindowSummary:
    key: str
    kind: str
    subject_type: str
    subject_id: str
    market_id: MarketId | str | None = None
    market_key: str | None = None
    instrument_id: InstrumentId | str | None = None
    qualifier: str = ""
    item_count: int = 0
    event_count: int = 0
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MarketWindowsView:
    total_count: int = 0
    windows: tuple[MarketWindowSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketSubscriptionSummary:
    key: str
    subject_type: str
    subject_id: str
    kind: str
    status: str
    fields: tuple[str, ...] = ()
    requested_by: str = "strategy"
    provider: str = ""
    stream: str = ""
    last_event_time: datetime | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class MarketSubscriptionsView:
    total_count: int = 0
    active_count: int = 0
    subscriptions: tuple[MarketSubscriptionSummary, ...] = ()


def market_window_schema(key: str, kind: str) -> ViewSchema:
    return ViewSchema(
        key,
        "market",
        fields=(
            ViewFieldSchema("subject_type", "market window subject type", "runtime state", "market event window state"),
            ViewFieldSchema("subject_id", "market window subject identity", "runtime state", "market event window state"),
            ViewFieldSchema("items", f"{kind} window items", "event time", "market event window state"),
            ViewFieldSchema("event_count", f"{kind} event count", "runtime sequence", "market event window state"),
            ViewFieldSchema("updated_at", f"latest {kind} event time", "event time", "market event window state"),
        ),
        mutability="runtime_writable",
        evidence=f"runtime market {kind} window state",
    )


@dataclass(frozen=True, slots=True)
class MarketViewReader:
    source: MarketViewSource

    def quotes(
        self,
        subject: MarketViewSubject,
        *,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> QuoteWindow:
        key = _market_key_for_subject(subject, exchange=exchange, market_type=market_type)
        value = self.source.get(MarketViewKeys.quotes(key), None)
        return value if isinstance(value, QuoteWindow) else _empty_quote_window(subject)

    def trades(
        self,
        subject: MarketViewSubject,
        *,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> TradeWindow:
        key = _market_key_for_subject(subject, exchange=exchange, market_type=market_type)
        value = self.source.get(MarketViewKeys.trades(key), None)
        return value if isinstance(value, TradeWindow) else _empty_trade_window(subject)

    def bars(
        self,
        subject: MarketViewSubject,
        *,
        timeframe: str | None = None,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> BarWindow:
        key = _market_key_for_subject(subject, exchange=exchange, market_type=market_type)
        if timeframe is not None:
            value = self.source.get(MarketViewKeys.bars(key, timeframe), None)
            return value if isinstance(value, BarWindow) else _empty_bar_window(subject, timeframe=timeframe)
        value = _latest_window(self.source, market_key=key, kind="bars")
        return value if isinstance(value, BarWindow) else _empty_bar_window(subject, timeframe="")

    def rates(
        self,
        subject: MarketViewSubject,
        *,
        basis: str | None = None,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> RateWindow:
        key = _market_key_for_subject(subject, exchange=exchange, market_type=market_type)
        if basis is not None:
            value = self.source.get(MarketViewKeys.rates(key, basis), None)
            return value if isinstance(value, RateWindow) else _empty_rate_window(subject, basis=basis)
        value = _latest_window(self.source, market_key=key, kind="rates")
        return value if isinstance(value, RateWindow) else _empty_rate_window(subject, basis="")

    def option_greeks(
        self,
        subject: MarketViewSubject,
        *,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> OptionGreeksWindow:
        key = _market_key_for_subject(subject, exchange=exchange, market_type=market_type)
        value = self.source.get(MarketViewKeys.option_greeks(key), None)
        return value if isinstance(value, OptionGreeksWindow) else _empty_option_greeks_window(subject)

    def orderbooks(
        self,
        subject: MarketViewSubject,
        *,
        exchange: MarketViewExchange | None = None,
        market_type: MarketViewMarketType | None = None,
    ) -> OrderBookWindow:
        key = _market_key_for_subject(subject, exchange=exchange, market_type=market_type)
        value = self.source.get(MarketViewKeys.orderbooks(key), None)
        return value if isinstance(value, OrderBookWindow) else _empty_orderbook_window(subject)


def _latest_window(source: MarketViewSource, *, market_key: str, kind: str) -> object | None:
    index = source.get(MarketViewKeys.windows, None)
    summaries = tuple(getattr(index, "windows", ()) or ())
    matches = tuple(item for item in summaries if getattr(item, "market_key", None) == market_key and getattr(item, "kind", None) == kind)
    if not matches:
        return None
    summary = max(matches, key=lambda item: item.updated_at or datetime.min)
    return source.get(summary.key, None)


def _market_key_for_subject(subject: MarketViewSubject, *, exchange: MarketViewExchange | None, market_type: MarketViewMarketType | None) -> str:
    if isinstance(subject, MarketRef):
        return subject.market_key
    normalized_id = _market_key_from_id(subject)
    if normalized_id is not None:
        return normalized_id
    try:
        resolved = MarketResolver(default_venue=None if exchange is None else str(exchange), default_market=None if market_type is None else str(market_type)).resolve(
            subject,
            venue=None if exchange is None else str(exchange),
            market=None if market_type is None else str(market_type),
        )
        return resolved.market_key
    except (KeyError, ValueError):
        return str(subject)


def _market_key_from_id(value: object) -> str | None:
    text = str(value)
    if text.startswith("market:"):
        return "_".join(text.removeprefix("market:").split(":"))
    return None


def _empty_quote_window(subject: MarketViewSubject) -> QuoteWindow:
    return QuoteWindow("unknown", str(subject))


def _empty_trade_window(subject: MarketViewSubject) -> TradeWindow:
    return TradeWindow("unknown", str(subject))


def _empty_bar_window(subject: MarketViewSubject, *, timeframe: str) -> BarWindow:
    return BarWindow("unknown", str(subject), timeframe=timeframe)


def _empty_rate_window(subject: MarketViewSubject, *, basis: str) -> RateWindow:
    return RateWindow("unknown", str(subject), basis=basis)


def _empty_option_greeks_window(subject: MarketViewSubject) -> OptionGreeksWindow:
    return OptionGreeksWindow("unknown", str(subject))


def _empty_orderbook_window(subject: MarketViewSubject) -> OrderBookWindow:
    return OrderBookWindow("unknown", str(subject))


def _decimal_diff(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous is None:
        return None
    return current - previous


def _spread(book: OrderBookSnapshot) -> Decimal | None:
    if book.bid1 is None or book.ask1 is None:
        return None
    return book.ask1.price - book.bid1.price


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in ("".join(character if character.isalnum() else "_" for character in text)).split("_") if part)


__all__ = [
    "BarWindow",
    "MarketSubscriptionSummary",
    "MarketSubscriptionsView",
    "MarketViewExchange",
    "MarketViewKeys",
    "MarketViewMarketType",
    "MarketViewReader",
    "MarketViewSource",
    "MarketViewSubject",
    "MarketWindow",
    "MarketWindowSummary",
    "MarketWindowsView",
    "OrderBookChangeSummary",
    "OrderBookWindow",
    "OptionGreeksWindow",
    "QuoteWindow",
    "RateWindow",
    "TradeWindow",
    "market_window_schema",
]
