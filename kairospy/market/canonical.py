from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from typing import Mapping, TypeAlias
from uuid import NAMESPACE_URL, UUID, uuid5

from kairospy.identity import InstrumentId
from kairospy.market.types import DerivativeMarketState, OrderBookDelta, OrderBookSnapshot, Quote, Trade


class MarketEventKind(StrEnum):
    TRADE = "trade"
    QUOTE = "quote"
    BAR = "bar"
    ORDER_BOOK_SNAPSHOT = "order_book_snapshot"
    ORDER_BOOK_DELTA = "order_book_delta"
    OPTION_SNAPSHOT = "option_snapshot"
    MARK_PRICE = "mark_price"
    INDEX_PRICE = "index_price"
    FUNDING_RATE = "funding_rate"
    OPEN_INTEREST = "open_interest"
    GREEKS = "greeks"
    INSTRUMENT_DEFINITION = "instrument_definition"
    TRADING_STATUS = "trading_status"
    STATISTICS = "statistics"
    DATA_WARNING = "data_warning"


@dataclass(frozen=True, slots=True)
class QuotePayload:
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    bid_exchange: str | None = None
    ask_exchange: str | None = None
    conditions: tuple[str, ...] = ()
    venue_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class TradePayload:
    price: Decimal
    size: Decimal
    trade_id: str | None = None
    side: str | None = None
    exchange: str | None = None
    conditions: tuple[str, ...] = ()
    venue_sequence: int | None = None
    original_trade_id: str | None = None
    correction: int = 0


@dataclass(frozen=True, slots=True)
class BarPayload:
    period_start: datetime
    period_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Decimal | None = None
    transactions: int | None = None

    def __post_init__(self) -> None:
        _aware(self.period_start, "period_start")
        _aware(self.period_end, "period_end")
        if self.period_end <= self.period_start:
            raise ValueError("bar period_end must be after period_start")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("bar OHLC values are inconsistent")
        if self.volume < 0:
            raise ValueError("bar volume cannot be negative")


@dataclass(frozen=True, slots=True)
class OrderBookLevelPayload:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookDeltaPayload:
    bids: tuple[OrderBookLevelPayload, ...]
    asks: tuple[OrderBookLevelPayload, ...]
    first_sequence: int
    last_sequence: int

    def __post_init__(self) -> None:
        if self.first_sequence < 0 or self.last_sequence < self.first_sequence:
            raise ValueError("order-book delta sequence range is invalid")


@dataclass(frozen=True, slots=True)
class OrderBookSnapshotPayload:
    bids: tuple[OrderBookLevelPayload, ...]
    asks: tuple[OrderBookLevelPayload, ...]
    sequence: int

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("order-book snapshot sequence cannot be negative")
        _validate_book_levels(self.bids, "bid")
        _validate_book_levels(self.asks, "ask")


@dataclass(frozen=True, slots=True)
class PricePayload:
    price: Decimal


@dataclass(frozen=True, slots=True)
class FundingRatePayload:
    rate: Decimal
    next_funding_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OpenInterestPayload:
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class GenericMarketPayload:
    """Immutable compatibility payload for event kinds awaiting dedicated records."""

    fields: tuple[tuple[str, object], ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> GenericMarketPayload:
        return cls(tuple(sorted(((str(key), _freeze(item)) for key, item in value.items()), key=lambda item: item[0])))

    def as_dict(self) -> dict[str, object]:
        return dict(self.fields)


MarketPayload: TypeAlias = (
    QuotePayload | TradePayload | BarPayload | OrderBookSnapshotPayload | OrderBookDeltaPayload | PricePayload
    | FundingRatePayload | OpenInterestPayload | GenericMarketPayload
)
CanonicalMarketPayload: TypeAlias = MarketPayload


__all__ = [
    "BarPayload",
    "CanonicalEventEnvelope",
    "CanonicalMarketPayload",
    "FundingRatePayload",
    "GenericMarketPayload",
    "MarketEventKind",
    "MarketPayload",
    "OpenInterestPayload",
    "OrderBookDeltaPayload",
    "OrderBookLevelPayload",
    "OrderBookSnapshotPayload",
    "PricePayload",
    "QuotePayload",
    "TradePayload",
    "canonical_from_trading_market_data",
    "canonicalize_market_event",
]


@dataclass(frozen=True, slots=True)
class CanonicalEventEnvelope:
    message_id: UUID
    schema_id: str
    schema_version: int
    kind: MarketEventKind
    instrument_id: InstrumentId
    payload: MarketPayload
    source: str
    source_instance: str
    stream_id: str
    partition_key: str
    event_time: datetime
    receive_time: datetime
    available_time: datetime
    published_time: datetime
    source_sequence: int | None = None
    receive_sequence: int | None = None
    canonical_sequence: int = 0
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    trace_id: UUID | None = None
    capture_offset: str | None = None
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("schema_id", "source", "source_instance", "stream_id", "partition_key"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"canonical event {name} cannot be empty")
        if self.schema_version < 1:
            raise ValueError("canonical event schema_version must be positive")
        if self.canonical_sequence < 0:
            raise ValueError("canonical_sequence cannot be negative")
        for name in ("source_sequence", "receive_sequence"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        for name in ("event_time", "receive_time", "available_time", "published_time"):
            _aware(getattr(self, name), name)
        if self.available_time < self.event_time:
            raise ValueError("available_time cannot precede event_time")
        if self.published_time < self.receive_time:
            raise ValueError("published_time cannot precede receive_time")

    @property
    def event_key(self) -> tuple[datetime, int, int, str]:
        sequence = self.source_sequence if self.source_sequence is not None else -1
        return self.available_time, sequence, self.canonical_sequence, str(self.message_id)


def canonicalize_market_event(event, *, source_instance: str = "default") -> CanonicalEventEnvelope:
    """Convert the persisted market event into the new canonical runtime contract."""

    kind = MarketEventKind(event.record_type.value)
    payload = _payload(kind, event.payload)
    receive_time = event.receive_time or event.available_time
    source_sequence = _optional_int(event.payload.get("sequence_number"))
    identity = {
        "source": event.source,
        "namespace": event.source_namespace,
        "source_instrument_id": event.source_instrument_id,
        "kind": kind.value,
        "event_time": event.event_time.isoformat(),
        "source_order": event.source_order,
        "payload": _primitive(payload),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=_json_default).encode()
    message_id = uuid5(NAMESPACE_URL, "kairospy:canonical-market-event:" + sha256(encoded).hexdigest())
    return CanonicalEventEnvelope(
        message_id, f"market.{kind.value}.v1", 1, kind, event.instrument_id, payload,
        event.source, source_instance, event.source_namespace, str(event.instrument_id),
        event.event_time, receive_time, event.available_time, event.ingested_at,
        source_sequence, event.source_order, 0, flags=event.flags,
    )


def canonical_from_trading_market_data(
    value: Quote | Trade | OrderBookSnapshot | OrderBookDelta | DerivativeMarketState,
    *,
    source: str,
    source_instance: str,
    stream_id: str,
    receive_time: datetime,
    published_time: datetime,
    source_sequence: int | None = None,
    receive_sequence: int | None = None,
) -> tuple[CanonicalEventEnvelope, ...]:
    """Normalize shared trading market values emitted by live venue connectors."""

    if isinstance(value, Quote):
        return (_canonical(
            MarketEventKind.QUOTE, value.instrument_id,
            QuotePayload(value.bid, value.ask, value.bid_size, value.ask_size), value.event_time,
            source, source_instance, stream_id, receive_time, published_time,
            source_sequence, receive_sequence, 0,
        ),)
    if isinstance(value, Trade):
        return (_canonical(
            MarketEventKind.TRADE, value.instrument_id,
            TradePayload(value.price, value.quantity), value.event_time,
            source, source_instance, stream_id, receive_time, published_time,
            source_sequence, receive_sequence, 0,
        ),)
    if isinstance(value, OrderBookSnapshot):
        return (_canonical(
            MarketEventKind.ORDER_BOOK_SNAPSHOT, value.instrument_id,
            OrderBookSnapshotPayload(
                tuple(OrderBookLevelPayload(item.price, item.quantity) for item in value.bids),
                tuple(OrderBookLevelPayload(item.price, item.quantity) for item in value.asks),
                value.sequence,
            ), value.event_time, source, source_instance, stream_id, receive_time, published_time,
            value.sequence, receive_sequence, 0,
        ),)
    if isinstance(value, OrderBookDelta):
        return (_canonical(
            MarketEventKind.ORDER_BOOK_DELTA, value.instrument_id,
            OrderBookDeltaPayload(
                tuple(OrderBookLevelPayload(item.price, item.quantity) for item in value.bid_updates),
                tuple(OrderBookLevelPayload(item.price, item.quantity) for item in value.ask_updates),
                value.first_sequence, value.last_sequence,
            ), value.event_time, source, source_instance, stream_id, receive_time, published_time,
            value.last_sequence, receive_sequence, 0,
        ),)
    if isinstance(value, DerivativeMarketState):
        events = []
        values = (
            (MarketEventKind.INDEX_PRICE, PricePayload(value.index_price) if value.index_price is not None else None),
            (MarketEventKind.MARK_PRICE, PricePayload(value.mark_price) if value.mark_price is not None else None),
            (MarketEventKind.FUNDING_RATE, FundingRatePayload(value.funding_rate, value.next_funding_at)
             if value.funding_rate is not None else None),
            (MarketEventKind.OPEN_INTEREST, OpenInterestPayload(value.open_interest)
             if value.open_interest is not None else None),
        )
        for canonical_sequence, (kind, payload) in enumerate(values):
            if payload is not None:
                events.append(_canonical(
                    kind, value.instrument_id, payload, value.event_time, source, source_instance, stream_id,
                    receive_time, published_time, source_sequence, receive_sequence, canonical_sequence,
                ))
        return tuple(events)
    raise TypeError(f"unsupported trading market data: {type(value).__name__}")


def _canonical(
    kind: MarketEventKind,
    instrument_id: InstrumentId,
    payload: MarketPayload,
    event_time: datetime,
    source: str,
    source_instance: str,
    stream_id: str,
    receive_time: datetime,
    published_time: datetime,
    source_sequence: int | None,
    receive_sequence: int | None,
    canonical_sequence: int,
) -> CanonicalEventEnvelope:
    material = {
        "kind": kind.value, "instrument": instrument_id.value, "payload": _primitive(payload),
        "source": source, "source_instance": source_instance, "stream_id": stream_id,
        "event_time": event_time.isoformat(), "source_sequence": source_sequence,
        "receive_sequence": receive_sequence, "canonical_sequence": canonical_sequence,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=_json_default).encode()
    message_id = uuid5(NAMESPACE_URL, "kairospy:canonical-live-market-event:" + sha256(encoded).hexdigest())
    return CanonicalEventEnvelope(
        message_id, f"market.{kind.value}.v1", 1, kind, instrument_id, payload,
        source, source_instance, stream_id, instrument_id.value,
        event_time, receive_time, max(event_time, receive_time), published_time,
        source_sequence, receive_sequence, canonical_sequence,
    )


def _payload(kind: MarketEventKind, value: Mapping[str, object]) -> MarketPayload:
    if kind is MarketEventKind.QUOTE:
        return QuotePayload(
            _decimal(value.get("bid")), _decimal(value.get("ask")),
            _decimal(value.get("bid_size")), _decimal(value.get("ask_size")),
            _optional_str(value.get("bid_exchange")), _optional_str(value.get("ask_exchange")),
            tuple(str(item) for item in value.get("conditions", ())),
            _optional_int(value.get("sequence_number")),
        )
    if kind is MarketEventKind.TRADE:
        price, size = _decimal(value.get("price")), _decimal(value.get("size"))
        if price is None or size is None:
            raise ValueError("trade payload requires price and size")
        return TradePayload(
            price, size, _optional_str(value.get("trade_id")), _optional_str(value.get("side")),
            _optional_str(value.get("exchange")), tuple(str(item) for item in value.get("conditions", ())),
            _optional_int(value.get("sequence_number")), _optional_str(value.get("original_id")),
            int(value.get("correction") or 0),
        )
    if kind is MarketEventKind.BAR:
        required = ("period_start", "period_end", "open", "high", "low", "close", "volume")
        missing = tuple(name for name in required if value.get(name) is None)
        if missing:
            raise ValueError("bar payload missing fields: " + ",".join(missing))
        return BarPayload(
            value["period_start"], value["period_end"],
            Decimal(str(value["open"])), Decimal(str(value["high"])), Decimal(str(value["low"])),
            Decimal(str(value["close"])), Decimal(str(value["volume"])), _decimal(value.get("vwap")),
            _optional_int(value.get("transactions")),
        )
    if kind is MarketEventKind.ORDER_BOOK_SNAPSHOT:
        sequence = _optional_int(value.get("sequence") or value.get("sequence_number"))
        if sequence is None:
            raise ValueError("order-book snapshot payload requires sequence")
        return OrderBookSnapshotPayload(
            _book_levels(value.get("bids")), _book_levels(value.get("asks")), sequence,
        )
    if kind is MarketEventKind.ORDER_BOOK_DELTA:
        first = _optional_int(value.get("first_sequence"))
        last = _optional_int(value.get("last_sequence") or value.get("sequence_number"))
        if first is None or last is None:
            raise ValueError("order-book delta payload requires first_sequence and last_sequence")
        return OrderBookDeltaPayload(
            _book_levels(value.get("bids"), allow_zero=True),
            _book_levels(value.get("asks"), allow_zero=True), first, last,
        )
    return GenericMarketPayload.from_mapping(value)


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(sorted(((str(key), _freeze(item)) for key, item in value.items()), key=lambda item: item[0]))
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (str, int, bool, Decimal, datetime, UUID)) or value is None:
        return value
    return str(value)


def _primitive(value: object) -> object:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_primitive(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _book_levels(value: object, *, allow_zero: bool = False) -> tuple[OrderBookLevelPayload, ...]:
    if value is None:
        return ()
    levels = []
    for item in value:
        if isinstance(item, Mapping):
            price, quantity = Decimal(str(item["price"])), Decimal(str(item["quantity"]))
        else:
            price, quantity = Decimal(str(item[0])), Decimal(str(item[1]))
        if price <= 0 or quantity < 0 or (quantity == 0 and not allow_zero):
            raise ValueError("order-book level has invalid price or quantity")
        levels.append(OrderBookLevelPayload(price, quantity))
    return tuple(levels)


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


def _validate_book_levels(levels: tuple[OrderBookLevelPayload, ...], side: str) -> None:
    prices: set[Decimal] = set()
    for level in levels:
        if level.price <= 0 or level.quantity <= 0:
            raise ValueError(f"order-book snapshot {side} levels require positive price and quantity")
        if level.price in prices:
            raise ValueError(f"order-book snapshot contains duplicate {side} price")
        prices.add(level.price)


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")
