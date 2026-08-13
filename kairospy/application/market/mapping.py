from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from . import (
    Bar,
    BarEvent,
    MarketEvent,
    Quote,
    QuoteEvent,
    Trade,
    TradeEvent,
)
from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import (
    EventMetadata,
    InstrumentId,
    MarketId,
    datetime_from_unix_nanos,
)


def map_market_event(raw, *, dispatch_sequence: int) -> MarketEvent:
    metadata = EventMetadata(
        stream_id=raw.stream_id,
        sequence=raw.sequence,
        dispatch_sequence=dispatch_sequence,
        schema_version=getattr(raw, "schema_version", 1),
        producer=getattr(raw, "producer", "market"),
        occurred_at=raw.occurred_at,
        occurred_at_unix_nanos=_event_nanos(raw.payload),
        causation_id=getattr(raw, "causation_id", None),
    )
    payload_kind = _payload_kind(raw.payload)
    if payload_kind is not None and payload_kind != raw.kind:
        raise ValueError(
            f"Market event discriminator {raw.kind!r} does not match "
            f"{type(raw.payload).__name__}"
        )
    value = map_market_view(raw.payload, kind=raw.kind)
    if raw.kind == "bar" and isinstance(value, Bar):
        return BarEvent(value, metadata)
    if raw.kind == "quote" and isinstance(value, Quote):
        return QuoteEvent(value, metadata)
    if raw.kind == "trade" and isinstance(value, Trade):
        return TradeEvent(value, metadata)
    raise ValueError(
        f"Market event discriminator {raw.kind!r} does not match {type(raw.payload).__name__}"
    )


def map_market_view(value: object, *, kind: str | None = None) -> Bar | Quote | Trade:
    if isinstance(value, (Bar, Quote, Trade)):
        if kind is not None and kind != _public_kind(value):
            raise ValueError(
                f"Market event discriminator {kind!r} does not match {type(value).__name__}"
            )
        return value
    raw = cast(Any, value)
    if kind == "bar" or (kind is None and hasattr(value, "timeframe")):
        _require_attributes(
            value,
            "instrument_id",
            "market_id",
            "timeframe",
            "open",
            "high",
            "low",
            "close",
            "event_time_unix_nanos",
        )
        occurred_at = datetime_from_unix_nanos(raw.event_time_unix_nanos)
        return Bar(
            market_id=MarketId(_required(raw.market_id, "bar market_id")),
            instrument=_instrument(raw.instrument_id),
            timeframe=raw.timeframe,
            open=Decimal(raw.open.value),
            high=Decimal(raw.high.value),
            low=Decimal(raw.low.value),
            close=Decimal(raw.close.value),
            volume=None if raw.volume is None else Decimal(raw.volume.value),
            occurred_at=occurred_at,
            occurred_at_unix_nanos=raw.event_time_unix_nanos,
            source_id=raw.source_id,
        )
    if kind == "quote" or (kind is None and hasattr(value, "bid_price")):
        _require_attributes(
            value,
            "instrument_id",
            "market_id",
            "bid_price",
            "ask_price",
            "event_time_unix_nanos",
        )
        occurred_at = datetime_from_unix_nanos(raw.event_time_unix_nanos)
        return Quote(
            market_id=MarketId(_required(raw.market_id, "quote market_id")),
            instrument=_instrument(raw.instrument_id),
            bid_price=_decimal(raw.bid_price),
            bid_quantity=_decimal(raw.bid_quantity),
            ask_price=_decimal(raw.ask_price),
            ask_quantity=_decimal(raw.ask_quantity),
            occurred_at=occurred_at,
            occurred_at_unix_nanos=raw.event_time_unix_nanos,
            source_id=raw.source_id,
        )
    if kind == "trade" or (kind is None and hasattr(value, "trade_id")):
        _require_attributes(
            value,
            "instrument_id",
            "market_id",
            "price",
            "quantity",
            "event_time_unix_nanos",
        )
        if raw.price is None or raw.quantity is None:
            raise ValueError("trade price and quantity are required")
        occurred_at = datetime_from_unix_nanos(raw.event_time_unix_nanos)
        return Trade(
            market_id=MarketId(_required(raw.market_id, "trade market_id")),
            instrument=_instrument(raw.instrument_id),
            price=Decimal(raw.price.value),
            quantity=Decimal(raw.quantity.value),
            aggressor_side=None,
            occurred_at=occurred_at,
            occurred_at_unix_nanos=raw.event_time_unix_nanos,
            source_id=raw.source_id,
        )
    raise TypeError(f"unsupported Market payload: {type(value).__name__}")


def _public_kind(value: Bar | Quote | Trade) -> str:
    if isinstance(value, Bar):
        return "bar"
    if isinstance(value, Quote):
        return "quote"
    return "trade"


def _payload_kind(value: object) -> str | None:
    if isinstance(value, (Bar, Quote, Trade)):
        return _public_kind(value)
    if hasattr(value, "timeframe"):
        return "bar"
    if hasattr(value, "bid_price") or hasattr(value, "ask_price"):
        return "quote"
    if hasattr(value, "trade_id"):
        return "trade"
    return None


def _require_attributes(value: object, *names: str) -> None:
    missing = [name for name in names if not hasattr(value, name)]
    if missing:
        raise TypeError(
            f"Market {type(value).__name__} is missing fields: {', '.join(missing)}"
        )


def _instrument(value: str) -> InstrumentRef:
    identifier = InstrumentId(value)
    return InstrumentRef(identifier, value.rsplit(":", 1)[-1])


def _required(value: str | None, name: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(value.value)


def _event_nanos(value: object) -> int | None:
    nanos = getattr(value, "event_time_unix_nanos", None)
    return nanos if isinstance(nanos, int) else None
