from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from types import MappingProxyType
from typing import Mapping, Sequence

from .model import MarketObservation


SUBSCRIPTION_KIND_FIELDS = "fields"

FIELD_QUOTE_BID = "quote.bid"
FIELD_QUOTE_ASK = "quote.ask"
FIELD_QUOTE_BID_SIZE = "quote.bid_size"
FIELD_QUOTE_ASK_SIZE = "quote.ask_size"
FIELD_QUOTE_MIDPOINT = "quote.midpoint"
FIELD_BOOK_BID1 = "book.bid1"
FIELD_BOOK_ASK1 = "book.ask1"
FIELD_BOOK_BID_DEPTH = "book.bid_depth"
FIELD_BOOK_ASK_DEPTH = "book.ask_depth"
FIELD_BAR_OPEN = "bar.open"
FIELD_BAR_HIGH = "bar.high"
FIELD_BAR_LOW = "bar.low"
FIELD_BAR_CLOSE = "bar.close"
FIELD_BAR_VOLUME = "bar.volume"
FIELD_TRADE_PRICE = "trade.price"
FIELD_TRADE_SIZE = "trade.size"
FIELD_TRADE_SIDE = "trade.side"
FIELD_TRADE_COST = "trade.cost"
FIELD_INTEREST_RATE = "interest_rate.rate"
FIELD_FUNDING_RATE = "funding_rate.rate"
FIELD_MARK_PRICE = "mark_price.value"
FIELD_INDEX_PRICE = "index_price.value"
FIELD_OPEN_INTEREST = "open_interest.value"

STREAM_TICKER = "ticker"
STREAM_ORDERBOOK = "orderbook"
STREAM_BAR = "bar"
STREAM_TRADE = "trade"
STREAM_MARKET_CONTEXT = "market_context"
STREAM_RATE = "rate"


@dataclass(frozen=True, slots=True)
class MarketDataField:
    path: str
    interval: str | None = None
    depth: int | None = None
    params: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("market data field path is required")
        if self.depth is not None and self.depth <= 0:
            raise ValueError("market data field depth must be positive")
        object.__setattr__(self, "path", _field_path(self.path))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    @property
    def family(self) -> str:
        return self.path.split(".", 1)[0]

    @property
    def key(self) -> str:
        parts = [self.path]
        if self.interval:
            parts.append(f"interval={self.interval}")
        if self.depth is not None:
            parts.append(f"depth={self.depth}")
        for name, value in sorted(self.params.items()):
            parts.append(f"{name}={value}")
        return "|".join(parts)


@dataclass(frozen=True, slots=True)
class MarketStreamPlan:
    key: str
    provider: str
    channel: str
    subject_type: str
    subject_id: str
    fields: tuple[MarketDataField, ...]
    identity: str | None = None
    params: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.channel.strip():
            raise ValueError("market stream plan key and channel are required")
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True, slots=True)
class MarketSubscriptionSpec:
    subject_type: str
    subject_id: str
    kind: str
    venue: str | None = None
    market: str | None = None
    source_symbol: str | None = None
    interval: str | None = None
    depth: int | None = None
    fields: Sequence[MarketDataField | str] = ()
    identity: str | None = None
    params: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.subject_type.strip() or not self.subject_id.strip() or not self.kind.strip():
            raise ValueError("market subscription subject and kind are required")
        if self.depth is not None and self.depth <= 0:
            raise ValueError("market subscription depth must be positive")
        fields = tuple(_coerce_field(field, interval=self.interval, depth=self.depth) for field in self.fields)
        if not fields:
            fields = _default_fields_for_kind(self.kind, interval=self.interval, depth=self.depth)
        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True, slots=True)
class MarketSubscription:
    key: str
    spec: MarketSubscriptionSpec
    status: str = "active"
    requested_by: str = "strategy"
    requested_at: datetime | None = None
    provider: str = ""
    stream: str = ""
    stream_plans: tuple[MarketStreamPlan, ...] = ()
    last_event_time: datetime | None = None
    error: str = ""

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def market_id(self) -> str:
        if self.spec.subject_type == "market":
            return self.spec.subject_id
        value = self.spec.params.get("market_id")
        return "" if value is None else str(value)

    @property
    def instrument_id(self) -> str:
        return self.spec.subject_id if self.spec.subject_type == "instrument" else ""

    @property
    def venue(self) -> str:
        return self.spec.venue or ""

    @property
    def market(self) -> str:
        return self.spec.market or ""

    @property
    def source_symbol(self) -> str:
        return self.spec.source_symbol or ""


class MarketSubscriptionRegistry:
    def __init__(self) -> None:
        self._subscriptions: dict[str, MarketSubscription] = {}

    def subscribe(self, spec: MarketSubscriptionSpec, *, requested_at: datetime | None = None) -> MarketSubscription:
        key = _subscription_key(spec)
        existing = self._subscriptions.get(key)
        if existing is not None:
            return existing
        stream_plans = plan_market_streams(spec)
        subscription = MarketSubscription(
            key=key,
            spec=spec,
            requested_at=requested_at,
            provider=spec.venue or "",
            stream=_subscription_stream(spec),
            stream_plans=stream_plans,
        )
        self._subscriptions[subscription.key] = subscription
        return subscription

    def subscribe_fields(
        self,
        subject_type: str,
        subject_id: str,
        fields: Sequence[MarketDataField | str],
        *,
        venue: str | None = None,
        market: str | None = None,
        source_symbol: str | None = None,
        requested_at: datetime | None = None,
        identity: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> MarketSubscription:
        return self.subscribe(
            MarketSubscriptionSpec(
                subject_type,
                subject_id,
                SUBSCRIPTION_KIND_FIELDS,
                venue=venue,
                market=market,
                source_symbol=source_symbol,
                fields=tuple(fields),
                identity=identity,
                params=params or {},
            ),
            requested_at=requested_at,
        )

    def unsubscribe(self, value: MarketSubscription | str) -> None:
        key = value.key if isinstance(value, MarketSubscription) else str(value)
        self._subscriptions.pop(key, None)

    def list(self) -> tuple[MarketSubscription, ...]:
        return tuple(self._subscriptions[key] for key in sorted(self._subscriptions))

    def observe(self, observation: MarketObservation) -> None:
        for key, subscription in tuple(self._subscriptions.items()):
            if subscription.spec.subject_type != observation.subject.subject_type:
                continue
            if subscription.spec.subject_id != observation.subject.subject_id:
                continue
            if not _subscription_observes_kind(subscription.spec, observation.kind):
                continue
            self._subscriptions[key] = MarketSubscription(
                subscription.key,
                subscription.spec,
                status=subscription.status,
                requested_by=subscription.requested_by,
                requested_at=subscription.requested_at,
                provider=subscription.provider,
                stream=subscription.stream,
                stream_plans=subscription.stream_plans,
                last_event_time=observation.observed_at,
                error=subscription.error,
            )


def plan_market_streams(spec: MarketSubscriptionSpec) -> tuple[MarketStreamPlan, ...]:
    fields_by_channel: dict[str, list[MarketDataField]] = {}
    for field in spec.fields:
        fields_by_channel.setdefault(_field_channel(field), []).append(field)
    plans: list[MarketStreamPlan] = []
    for channel, fields in sorted(fields_by_channel.items()):
        params = dict(spec.params)
        if spec.interval is not None:
            params.setdefault("interval", spec.interval)
        if spec.depth is not None:
            params.setdefault("depth", spec.depth)
        stream_key = _stream_plan_key(spec, channel, tuple(fields), params)
        plans.append(
            MarketStreamPlan(
                stream_key,
                spec.venue or "",
                channel,
                spec.subject_type,
                spec.subject_id,
                tuple(fields),
                identity=spec.identity,
                params=params,
            )
        )
    return tuple(plans)


def _subscription_key(spec: MarketSubscriptionSpec) -> str:
    suffix = _key_part(spec.subject_id)
    if spec.source_symbol:
        suffix = _key_part(f"{spec.venue}_{spec.market}_{spec.source_symbol}")
    if spec.kind == SUBSCRIPTION_KIND_FIELDS:
        fields_key = sha1("|".join(field.key for field in spec.fields).encode("utf-8")).hexdigest()[:12]
        identity = "" if spec.identity is None else f".{_key_part(spec.identity)}"
        return f"market.fields.{suffix}.{fields_key}{identity}"
    return f"market.{_key_part(spec.kind)}.{suffix}"


def _subscription_stream(spec: MarketSubscriptionSpec) -> str:
    parts = ("market", spec.kind, spec.venue, spec.market, spec.source_symbol or spec.subject_id)
    return ".".join(_key_part(part) for part in parts if part)


def _key_part(value: object) -> str:
    return "".join(character if character.isalnum() else "_" for character in str(value).lower()).strip("_")


def _field_path(value: object) -> str:
    return ".".join(_key_part(part) for part in str(value).split(".") if _key_part(part))


def _coerce_field(value: MarketDataField | str, *, interval: str | None = None, depth: int | None = None) -> MarketDataField:
    if isinstance(value, MarketDataField):
        return value
    return MarketDataField(str(value), interval=interval, depth=depth)


def _default_fields_for_kind(kind: str, *, interval: str | None, depth: int | None) -> tuple[MarketDataField, ...]:
    defaults = {
        "quote": (FIELD_QUOTE_BID, FIELD_QUOTE_ASK, FIELD_QUOTE_BID_SIZE, FIELD_QUOTE_ASK_SIZE, FIELD_QUOTE_MIDPOINT),
        "orderbook": (FIELD_BOOK_BID1, FIELD_BOOK_ASK1, FIELD_BOOK_BID_DEPTH, FIELD_BOOK_ASK_DEPTH),
        "bar": (FIELD_BAR_OPEN, FIELD_BAR_HIGH, FIELD_BAR_LOW, FIELD_BAR_CLOSE, FIELD_BAR_VOLUME),
        "trade": (FIELD_TRADE_PRICE, FIELD_TRADE_SIZE, FIELD_TRADE_SIDE, FIELD_TRADE_COST),
        "interest_rate": (FIELD_INTEREST_RATE,),
        "funding_rate": (FIELD_FUNDING_RATE,),
    }.get(kind, (f"{kind}.value",))
    return tuple(MarketDataField(path, interval=interval, depth=depth) for path in defaults)


def _field_channel(field: MarketDataField) -> str:
    family = field.family
    if family in {"quote", "ticker"}:
        return STREAM_TICKER
    if family == "book":
        return STREAM_ORDERBOOK
    if family == "bar":
        return STREAM_BAR
    if family == "trade":
        return STREAM_TRADE
    if family in {"funding_rate", "mark_price", "index_price", "open_interest"}:
        return STREAM_MARKET_CONTEXT
    if family == "interest_rate":
        return STREAM_RATE
    return family


def _subscription_observes_kind(spec: MarketSubscriptionSpec, kind: str) -> bool:
    if spec.kind != SUBSCRIPTION_KIND_FIELDS:
        return spec.kind == kind
    observed_family = {
        "ticker": "quote",
        "quote": "quote",
        "orderbook": "book",
        "bar": "bar",
        "ohlcv": "bar",
        "trade": "trade",
        "interest_rate": "interest_rate",
        "funding_rate": "funding_rate",
        "mark_price": "mark_price",
        "index_price": "index_price",
        "open_interest": "open_interest",
    }.get(kind, kind)
    return any(field.family == observed_family for field in spec.fields)


def _stream_plan_key(
    spec: MarketSubscriptionSpec,
    channel: str,
    fields: tuple[MarketDataField, ...],
    params: Mapping[str, object],
) -> str:
    subject = _key_part(spec.source_symbol or spec.subject_id)
    identity = "" if spec.identity is None else f".{_key_part(spec.identity)}"
    options = "|".join(
        [field.key for field in fields]
        + [f"{name}={value}" for name, value in sorted(params.items())]
    )
    digest = sha1(options.encode("utf-8")).hexdigest()[:12]
    return ".".join(part for part in ("market", spec.venue, channel, subject + identity, digest) if part)


__all__ = [
    "FIELD_BAR_CLOSE",
    "FIELD_BAR_HIGH",
    "FIELD_BAR_LOW",
    "FIELD_BAR_OPEN",
    "FIELD_BAR_VOLUME",
    "FIELD_BOOK_ASK1",
    "FIELD_BOOK_ASK_DEPTH",
    "FIELD_BOOK_BID1",
    "FIELD_BOOK_BID_DEPTH",
    "FIELD_FUNDING_RATE",
    "FIELD_INDEX_PRICE",
    "FIELD_INTEREST_RATE",
    "FIELD_MARK_PRICE",
    "FIELD_OPEN_INTEREST",
    "FIELD_QUOTE_ASK",
    "FIELD_QUOTE_ASK_SIZE",
    "FIELD_QUOTE_BID",
    "FIELD_QUOTE_BID_SIZE",
    "FIELD_QUOTE_MIDPOINT",
    "FIELD_TRADE_COST",
    "FIELD_TRADE_PRICE",
    "FIELD_TRADE_SIDE",
    "FIELD_TRADE_SIZE",
    "STREAM_BAR",
    "STREAM_MARKET_CONTEXT",
    "STREAM_ORDERBOOK",
    "STREAM_RATE",
    "STREAM_TICKER",
    "STREAM_TRADE",
    "SUBSCRIPTION_KIND_FIELDS",
    "MarketDataField",
    "MarketSubscription",
    "MarketSubscriptionRegistry",
    "MarketSubscriptionSpec",
    "MarketStreamPlan",
    "plan_market_streams",
]
