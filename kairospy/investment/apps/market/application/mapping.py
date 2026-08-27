from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from . import (
    Bar,
    BarEvent,
    GreeksEvent,
    MarketEvent,
    OptionGreeks,
    ObservationScope,
    Quote,
    QuoteEvent,
    Trade,
    TradeEvent,
)
from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.investment.application.eventing import EventMetadata
from kairospy.primitives.reference import InstrumentId
from kairospy.primitives.time import datetime_from_unix_nanos
from kairospy.infrastructure.contracts.market.view import (
    MarketBarCurrent,
    MarketGreeksCurrent,
    MarketObservationScope as ContractObservationScope,
    MarketQuoteCurrent,
)


def map_market_event(raw) -> MarketEvent:
    metadata = EventMetadata(
        stream_id=raw.stream_id,
        sequence=raw.sequence,
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
    if raw.kind == "greeks" and isinstance(value, OptionGreeks):
        return GreeksEvent(value, metadata)
    raise ValueError(
        f"Market event discriminator {raw.kind!r} does not match {type(raw.payload).__name__}"
    )


def map_market_view(
    value: object, *, kind: str | None = None
) -> Bar | Quote | Trade | OptionGreeks:
    if isinstance(value, (Bar, Quote, Trade, OptionGreeks)):
        if kind is not None and kind != _public_kind(value):
            raise ValueError(
                f"Market event discriminator {kind!r} does not match {type(value).__name__}"
            )
        return value
    if isinstance(value, MarketBarCurrent):
        return Bar(
            scope=_contract_scope(value.scope),
            instrument=_instrument(value.instrument_id),
            timeframe=value.bar_spec_id,
            open=value.open,
            high=value.high,
            low=value.low,
            close=value.close,
            volume=value.volume,
            occurred_at=datetime_from_unix_nanos(value.source_observed_at_unix_nanos),
            occurred_at_unix_nanos=value.source_observed_at_unix_nanos,
            provider=value.provider,
        )
    if isinstance(value, MarketQuoteCurrent):
        return Quote(
            scope=_contract_scope(value.scope),
            instrument=_instrument(value.instrument_id),
            bid_price=value.bid_price,
            bid_quantity=value.bid_quantity,
            ask_price=value.ask_price,
            ask_quantity=value.ask_quantity,
            occurred_at=datetime_from_unix_nanos(value.source_observed_at_unix_nanos),
            occurred_at_unix_nanos=value.source_observed_at_unix_nanos,
            provider=value.provider,
            bid_venue_code=value.bid_venue_code,
            ask_venue_code=value.ask_venue_code,
            tape=value.tape,
        )
    if isinstance(value, MarketGreeksCurrent):
        if value.expiry_unix_nanos is None:
            raise ValueError("Market Greeks expiry is required by the Strategy API")
        return OptionGreeks(
            scope=_contract_scope(value.scope),
            instrument=_instrument(value.instrument_id),
            expiry_unix_nanos=value.expiry_unix_nanos,
            strike=value.strike,
            delta=value.delta,
            gamma=value.gamma,
            vega=value.vega,
            theta=value.theta,
            implied_volatility=value.implied_volatility,
            occurred_at=datetime_from_unix_nanos(value.source_observed_at_unix_nanos),
            occurred_at_unix_nanos=value.source_observed_at_unix_nanos,
            provider=value.provider,
            derivation=value.derivation_id,
        )
    raw = cast(Any, value)
    kind = kind or _payload_kind(value)
    if kind == "bar":
        event_time = _fb_nanos(raw, "SourceObservedAtUnixNanos")
        return Bar(
            scope=_fb_scope(raw),
            instrument=_instrument(_fb_required_text(raw, "InstrumentId")),
            timeframe=_fb_required_text(raw, "BarSpecId"),
            open=_fb_required_decimal(raw, "Open"),
            high=_fb_required_decimal(raw, "High"),
            low=_fb_required_decimal(raw, "Low"),
            close=_fb_required_decimal(raw, "Close"),
            volume=_fb_decimal(raw, "Volume"),
            occurred_at=datetime_from_unix_nanos(event_time),
            occurred_at_unix_nanos=event_time,
            provider=_fb_text(raw, "Provider"),
        )
    if kind == "quote":
        event_time = _fb_nanos(raw, "SourceObservedAtUnixNanos")
        return Quote(
            scope=_fb_scope(raw),
            instrument=_instrument(_fb_required_text(raw, "InstrumentId")),
            bid_price=_fb_decimal(raw, "BidPrice"),
            bid_quantity=_fb_decimal(raw, "BidQuantity"),
            ask_price=_fb_decimal(raw, "AskPrice"),
            ask_quantity=_fb_decimal(raw, "AskQuantity"),
            occurred_at=datetime_from_unix_nanos(event_time),
            occurred_at_unix_nanos=event_time,
            provider=_fb_text(raw, "Provider"),
            bid_venue_code=_fb_text(raw, "BidVenueCode"),
            ask_venue_code=_fb_text(raw, "AskVenueCode"),
            tape=raw.Tape() or None,
        )
    if kind == "trade":
        event_time = _fb_nanos(raw, "SourceObservedAtUnixNanos")
        return Trade(
            scope=_fb_scope(raw),
            instrument=_instrument(_fb_required_text(raw, "InstrumentId")),
            price=_fb_required_decimal(raw, "Price"),
            quantity=_fb_required_decimal(raw, "Quantity"),
            aggressor_side=None,
            occurred_at=datetime_from_unix_nanos(event_time),
            occurred_at_unix_nanos=event_time,
            provider=_fb_text(raw, "Provider"),
            venue_code=_fb_text(raw, "VenueCode"),
            tape=raw.Tape() or None,
            trf_id=raw.TrfId() or None,
            participant_timestamp_unix_nanos=(
                raw.ParticipantTimestampUnixNanos() or None
            ),
            trf_timestamp_unix_nanos=raw.TrfTimestampUnixNanos() or None,
        )
    if kind == "greeks":
        event_time = _fb_nanos(raw, "SourceObservedAtUnixNanos")
        return OptionGreeks(
            scope=_fb_scope(raw),
            instrument=_instrument(_fb_required_text(raw, "InstrumentId")),
            expiry_unix_nanos=int(raw.ExpiryUnixNanos()),
            strike=_fb_decimal(raw, "Strike"),
            delta=_fb_decimal(raw, "Delta"),
            gamma=_fb_decimal(raw, "Gamma"),
            vega=_fb_decimal(raw, "Vega"),
            theta=_fb_decimal(raw, "Theta"),
            implied_volatility=_fb_decimal(raw, "ImpliedVolatility"),
            occurred_at=datetime_from_unix_nanos(event_time),
            occurred_at_unix_nanos=event_time,
            provider=_fb_text(raw, "Provider"),
            derivation=_fb_text(raw, "DerivationId"),
        )
    raise TypeError(f"unsupported Market payload: {type(value).__name__}")


def _public_kind(value: Bar | Quote | Trade | OptionGreeks) -> str:
    if isinstance(value, Bar):
        return "bar"
    if isinstance(value, Quote):
        return "quote"
    if isinstance(value, Trade):
        return "trade"
    return "greeks"


def _payload_kind(value: object) -> str | None:
    if isinstance(value, (Bar, Quote, Trade, OptionGreeks)):
        return _public_kind(value)
    if isinstance(value, MarketBarCurrent):
        return "bar"
    if isinstance(value, MarketQuoteCurrent):
        return "quote"
    if isinstance(value, MarketGreeksCurrent):
        return "greeks"
    if callable(getattr(value, "BarSpecId", None)):
        return "bar"
    if callable(getattr(value, "BidPrice", None)):
        return "quote"
    if callable(getattr(value, "TradeId", None)):
        return "trade"
    if callable(getattr(value, "ImpliedVolatility", None)):
        return "greeks"
    return None


def _instrument(value: str) -> InstrumentRef:
    identifier = InstrumentId(value)
    return InstrumentRef(identifier, value.rsplit(":", 1)[-1])


def _contract_scope(value: ContractObservationScope) -> ObservationScope:
    if value.kind == "market" and value.market_id is not None:
        return ObservationScope.market(value.market_id)
    if value.kind == "consolidated" and value.instrument_id is not None:
        return ObservationScope.consolidated(value.instrument_id, value.network_id)
    raise ValueError("Market contract observation scope is invalid")


def _fb_scope(value: Any) -> ObservationScope:
    scope = value.Scope()
    if scope is None:
        raise ValueError("Market observation scope is required")
    kind = int(scope.Kind())
    if kind == 1:
        return ObservationScope.market(_fb_required_text(scope, "MarketId"))
    if kind == 2:
        return ObservationScope.consolidated(
            _fb_required_text(scope, "InstrumentId"),
            _fb_text(scope, "NetworkId"),
        )
    raise ValueError(f"unknown Market observation scope kind: {kind}")


def _fb_text(value: Any, name: str) -> str | None:
    raw = getattr(value, name)()
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def _fb_required_text(value: Any, name: str) -> str:
    result = _fb_text(value, name)
    if result is None or not result.strip():
        raise ValueError(f"Market {name} is required")
    return result


def _fb_decimal(value: Any, name: str) -> Decimal | None:
    raw = getattr(value, name)()
    if raw is None:
        return None
    return Decimal(int(raw.Mantissa())).scaleb(-int(raw.Scale()))


def _fb_required_decimal(value: Any, name: str) -> Decimal:
    result = _fb_decimal(value, name)
    if result is None:
        raise ValueError(f"Market {name} is required")
    return result


def _fb_nanos(value: Any, name: str) -> int:
    result = int(getattr(value, name)())
    if result < 0:
        raise ValueError(f"Market {name} cannot be negative")
    return result


def _event_nanos(value: object) -> int | None:
    if isinstance(value, (Bar, Quote, Trade, OptionGreeks)):
        return value.occurred_at_unix_nanos
    if isinstance(value, (MarketBarCurrent, MarketQuoteCurrent, MarketGreeksCurrent)):
        return value.source_observed_at_unix_nanos
    accessor = getattr(value, "SourceObservedAtUnixNanos", None)
    if not callable(accessor):
        return None
    observed_at = accessor()
    if not isinstance(observed_at, int):
        raise ValueError("Market SourceObservedAtUnixNanos must be an integer")
    return observed_at
