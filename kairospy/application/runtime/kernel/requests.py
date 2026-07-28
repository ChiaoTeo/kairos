from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping, Protocol

from kairospy.core.market import MarketEvent, MarketSubject, Quote
from kairospy.core.reference import MarketRef, MarketResolver

from ..model import RuntimeDataEnvelope


class MarketDataRequestProvider(Protocol):
    def fetch_quote(
        self,
        market: MarketRef,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object] | Quote | None:
        ...


@dataclass(frozen=True, slots=True)
class RuntimeRequestProviders:
    market_data: MarketDataRequestProvider | None = None


class MarketRequestService:
    def __init__(
        self,
        resolver: MarketResolver,
        *,
        phase: str,
        providers: RuntimeRequestProviders | None = None,
        emit_event: Callable[[RuntimeDataEnvelope], None] | None = None,
    ) -> None:
        self.resolver = resolver
        self.phase = phase
        self.providers = providers or RuntimeRequestProviders()
        self.emit_event = emit_event
        self._sequence = 0

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
        provider = self.providers.market_data
        if provider is None:
            raise RuntimeError("runtime has no market data request provider")
        resolved = self.resolver.resolve(instrument, venue=venue, market=market)
        value = provider.fetch_quote(resolved, params=params)
        if value is None:
            return None
        quote = value if isinstance(value, Quote) else quote_from_mapping(
            value,
            instrument_id=resolved.instrument_id,
            market_id=resolved.market_id,
            market_key=resolved.market_key,
            source=str(value.get("source") or resolved.venue),
        )
        if self.emit_event is not None:
            self._sequence += 1
            self.emit_event(quote_data_envelope(quote, sequence=self._sequence))
        return quote


def quote_data_envelope(quote: Quote, *, sequence: int) -> RuntimeDataEnvelope:
    return RuntimeDataEnvelope(
        "market",
        "quote",
        quote.time,
        sequence,
        quote_market_event(quote, sequence=sequence),
        stream=f"market.request.quote.{quote.market_key or quote.market_id or quote.instrument_id}",
        source=quote.source,
        metadata={"request": "quote"},
    )


def quote_market_event(quote: Quote, *, sequence: int) -> MarketEvent:
    return MarketEvent(
        MarketSubject("instrument", quote.instrument_id),
        quote.time,
        quote,
        source=quote.source,
        available_at=quote.time,
        sequence=sequence,
        metadata={"request": "quote"},
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
    "MarketDataRequestProvider",
    "MarketRequestService",
    "RuntimeRequestProviders",
    "quote_data_envelope",
    "quote_from_mapping",
    "quote_market_event",
]
