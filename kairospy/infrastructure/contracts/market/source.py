from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import struct
import sys
from typing import Any, AsyncIterator, cast

from kairospy.infrastructure.contracts.market.records import MarketEventRecord
from kairospy.infrastructure.contracts.market import (
    MarketIndexedViewQueries,
    MarketViewKey,
    MarketViewKind,
)
from kairospy.infrastructure.transport.native_event import NativeEventSource
from kairospy.infrastructure.protocol.generated_spec import (
    DEFAULT_CHANNEL,
    MARKET_EVENTS,
)

# The generated FlatBuffers modules use their schema namespace (``kairos``)
# for sibling imports. Keep that generated namespace private to this adapter
# while making those imports resolvable; application code never sees it.
from kairospy.infrastructure.protocol.generated import kairos as _generated_kairos

sys.modules.setdefault("kairos", _generated_kairos)


@dataclass(frozen=True, slots=True)
class DecimalValue:
    mantissa: int
    scale: int

    @property
    def value(self) -> str:
        digits = str(abs(self.mantissa)).rjust(self.scale + 1, "0")
        if self.scale == 0:
            result = digits
        else:
            result = f"{digits[: -self.scale]}.{digits[-self.scale :]}"
        return result if self.mantissa >= 0 else f"-{result}"


@dataclass(frozen=True, slots=True)
class ObservationScopeView:
    kind: str
    market_id: str | None = None
    instrument_id: str | None = None
    network_id: str | None = None


@dataclass(frozen=True, slots=True)
class QuoteView:
    instrument_id: str
    scope: ObservationScopeView
    bid_price: DecimalValue | None
    bid_quantity: DecimalValue | None
    ask_price: DecimalValue | None
    ask_quantity: DecimalValue | None
    event_time_unix_nanos: int
    provider: str | None
    bid_venue_code: str | None = None
    ask_venue_code: str | None = None
    tape: int | None = None


@dataclass(frozen=True, slots=True)
class TradeView:
    instrument_id: str
    scope: ObservationScopeView
    trade_id: str | None
    price: DecimalValue | None
    quantity: DecimalValue | None
    event_time_unix_nanos: int
    provider: str | None
    venue_code: str | None = None
    tape: int | None = None
    trf_id: int | None = None
    participant_timestamp_unix_nanos: int | None = None
    trf_timestamp_unix_nanos: int | None = None


@dataclass(frozen=True, slots=True)
class PriceLevelView:
    price: DecimalValue
    quantity: DecimalValue


@dataclass(frozen=True, slots=True)
class OrderBookView:
    market_id: str
    instrument_id: str
    provider: str | None
    sequence: int
    first_sequence: int
    last_sequence: int
    event_time_unix_nanos: int
    synchronized: bool
    depth_policy: str | None
    checksum: str | None
    bids: tuple[PriceLevelView, ...]
    asks: tuple[PriceLevelView, ...]


@dataclass(frozen=True, slots=True)
class BarView:
    instrument_id: str
    scope: ObservationScopeView
    timeframe: str
    open: DecimalValue
    high: DecimalValue
    low: DecimalValue
    close: DecimalValue
    volume: DecimalValue | None
    event_time_unix_nanos: int
    provider: str | None
    derivation: str | None


@dataclass(frozen=True, slots=True)
class GreeksView:
    instrument_id: str
    scope: ObservationScopeView
    expiry_unix_nanos: int
    strike: DecimalValue | None
    delta: DecimalValue | None
    gamma: DecimalValue | None
    vega: DecimalValue | None
    theta: DecimalValue | None
    implied_volatility: DecimalValue | None
    event_time_unix_nanos: int
    provider: str | None
    derivation: str | None


@dataclass(frozen=True, slots=True)
class MarketDataView:
    quotes: tuple[QuoteView, ...]
    trades: tuple[TradeView, ...] = ()
    bars: tuple[BarView, ...] = ()
    greeks: tuple[GreeksView, ...] = ()

    def current(self, instrument_id: str) -> QuoteView | None:
        return next(
            (quote for quote in self.quotes if quote.instrument_id == instrument_id),
            None,
        )


class MarketViewAccess:
    """Access Market v2 entities from the owner-scoped LMDB view."""

    def __init__(
        self,
        path: str | Path,
        *,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
    ) -> None:
        self.path = Path(path)
        self._queries = MarketIndexedViewQueries(
            path,
            workspace_id=workspace_id,
            launch_id=launch_id,
            instance_id=instance_id,
        )

    def read_view(
        self,
        key: MarketViewKey | str,
        provider: str | None = None,
        kind: MarketViewKind | None = None,
        qualifier: str | None = None,
    ):
        """Read one typed v2 current-view resource."""

        if isinstance(key, str):
            if provider is None or kind is None:
                raise ValueError("Market v2 view source and kind are required")
            key = MarketViewKey(key, provider, kind, qualifier)
        return self._queries.read(key)

    def read_quote(self, market_id: str, provider: str) -> QuoteView | None:
        frame = self.read_view(
            MarketViewKey(market_id, provider, MarketViewKind.QUOTE)
        )
        if frame is None:
            return None
        return _decode_quote(cast(Any, frame.value))

    def read_bar(
        self, market_id: str, provider: str, timeframe: str
    ) -> BarView | None:
        frame = self.read_view(
            MarketViewKey(market_id, provider, MarketViewKind.BAR, timeframe)
        )
        if frame is None:
            return None
        return _decode_bar(cast(Any, frame.value))

    def read_greeks(self, market_id: str, provider: str) -> GreeksView | None:
        frame = self.read_view(
            MarketViewKey(market_id, provider, MarketViewKind.GREEKS)
        )
        if frame is None:
            return None
        return _decode_greeks(cast(Any, frame.value))


def _scope(value: object) -> ObservationScopeView:
    raw = cast(Any, value).Scope()
    if raw is None:
        raise ValueError("observation scope is required")

    def text(name: str) -> str | None:
        item = getattr(raw, name)()
        return None if item is None else item.decode()

    kind = raw.Kind()
    if kind == 1:
        market_id = text("MarketId")
        if market_id is None:
            raise ValueError("market observation scope has no market_id")
        return ObservationScopeView("market", market_id=market_id)
    if kind == 2:
        instrument_id = text("InstrumentId")
        if instrument_id is None:
            raise ValueError("consolidated observation scope has no instrument_id")
        return ObservationScopeView(
            "consolidated",
            instrument_id=instrument_id,
            network_id=text("NetworkId"),
        )
    raise ValueError(f"unknown observation scope kind: {kind}")


def _decode_quote(value: object) -> QuoteView:
    value = cast(Any, value)

    def text(name: str) -> str | None:
        raw = getattr(value, name)()
        return None if raw is None else raw.decode()

    def decimal(name: str) -> DecimalValue | None:
        raw = getattr(value, name)()
        return None if raw is None else DecimalValue(raw.Mantissa(), raw.Scale())

    event_time = getattr(value, "EventTimeUnixNanos", None)
    if event_time is None:
        event_time = getattr(value, "SourceObservedAtUnixNanos")
    return QuoteView(
        instrument_id=text("InstrumentId") or "",
        scope=_scope(value),
        bid_price=decimal("BidPrice"),
        bid_quantity=decimal("BidQuantity"),
        ask_price=decimal("AskPrice"),
        ask_quantity=decimal("AskQuantity"),
        event_time_unix_nanos=event_time(),
        provider=text("Provider"),
        bid_venue_code=text("BidVenueCode"),
        ask_venue_code=text("AskVenueCode"),
        tape=getattr(value, "Tape")() or None,
    )


def _decode_trade(value: object) -> TradeView:
    value = cast(Any, value)

    def text(name: str) -> str | None:
        raw = getattr(value, name)()
        return None if raw is None else raw.decode()

    def decimal(name: str) -> DecimalValue | None:
        raw = getattr(value, name)()
        return None if raw is None else DecimalValue(raw.Mantissa(), raw.Scale())

    return TradeView(
        instrument_id=text("InstrumentId") or "",
        scope=_scope(value),
        trade_id=text("TradeId"),
        price=decimal("Price"),
        quantity=decimal("Quantity"),
        event_time_unix_nanos=getattr(value, "EventTimeUnixNanos")(),
        provider=text("Provider"),
        venue_code=text("VenueCode"),
        tape=getattr(value, "Tape")() or None,
        trf_id=getattr(value, "TrfId")() or None,
        participant_timestamp_unix_nanos=(
            getattr(value, "ParticipantTimestampUnixNanos")() or None
        ),
        trf_timestamp_unix_nanos=getattr(value, "TrfTimestampUnixNanos")() or None,
    )


def _decode_bar(value: object) -> BarView:
    value = cast(Any, value)

    def text(name: str) -> str | None:
        raw = getattr(value, name)()
        return None if raw is None else raw.decode()

    def decimal(name: str) -> DecimalValue:
        raw = getattr(value, name)()
        return DecimalValue(raw.Mantissa(), raw.Scale())

    def optional_decimal(name: str) -> DecimalValue | None:
        raw = getattr(value, name)()
        return None if raw is None else DecimalValue(raw.Mantissa(), raw.Scale())

    return BarView(
        instrument_id=text("InstrumentId") or "",
        scope=_scope(value),
        timeframe=text("BarSpecId") or "",
        open=decimal("Open"),
        high=decimal("High"),
        low=decimal("Low"),
        close=decimal("Close"),
        volume=optional_decimal("Volume"),
        event_time_unix_nanos=getattr(value, "SourceObservedAtUnixNanos")(),
        provider=text("Provider"),
        derivation=None,
    )


def _decode_greeks(value: object) -> GreeksView:
    value = cast(Any, value)

    def text(name: str) -> str | None:
        raw = getattr(value, name)()
        return None if raw is None else raw.decode()

    def decimal(name: str) -> DecimalValue | None:
        raw = getattr(value, name)()
        return None if raw is None else DecimalValue(raw.Mantissa(), raw.Scale())

    return GreeksView(
        instrument_id=text("InstrumentId") or "",
        scope=_scope(value),
        expiry_unix_nanos=getattr(value, "ExpiryUnixNanos")(),
        strike=decimal("Strike"),
        delta=decimal("Delta"),
        gamma=decimal("Gamma"),
        vega=decimal("Vega"),
        theta=decimal("Theta"),
        implied_volatility=decimal("ImpliedVolatility"),
        event_time_unix_nanos=getattr(value, "SourceObservedAtUnixNanos")(),
        provider=text("Provider"),
        derivation=text("DerivationId"),
    )


class UnixMarketEventStream:
    """Consume the replay Market frame stream.

    Production Market events use Aeron.  This adapter remains only for the
    launch-owned replay process and never reads or recovers through a current view.
    """

    def __init__(
        self,
        socket_path: str | Path,
        *,
        stream_id: str = "market.events",
        replayable: bool = False,
        reconnect_delay: float = 0.25,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.stream_id = stream_id
        self.replayable = replayable
        if reconnect_delay < 0:
            raise ValueError("reconnect_delay cannot be negative")
        self.reconnect_delay = reconnect_delay

    async def replay_from(
        self, after_sequence: int = 0
    ) -> AsyncIterator[MarketEventRecord]:
        cursor = max(0, after_sequence)
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(self.socket_path)
            except (FileNotFoundError, ConnectionError, OSError):
                await asyncio.sleep(self.reconnect_delay)
                continue
            try:
                while True:
                    prefix = await reader.readexactly(4)
                    (length,) = struct.unpack(">I", prefix)
                    if length == 0 or length > 4 * 1024 * 1024:
                        raise ValueError("invalid market event frame length")
                    payload = await reader.readexactly(length)
                    event = _decode_market_event(payload)
                    if event.sequence <= cursor:
                        continue
                    cursor = event.sequence
                    yield event
            except asyncio.IncompleteReadError:
                if self.replayable:
                    return
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
            await asyncio.sleep(self.reconnect_delay)


class AeronMarketEventSource(NativeEventSource[MarketEventRecord]):
    """Market-owned adapter over the native Aeron subscription bridge."""

    replayable = False

    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = DEFAULT_CHANNEL,
        stream_id: int = MARKET_EVENTS,
    ) -> None:
        super().__init__(
            decoder=decode_market_event,
            aeron_dir=aeron_dir,
            channel=channel,
            stream_id=stream_id,
        )


def decode_market_event(payload: bytes) -> MarketEventRecord:
    identifier = payload[4:8]
    v2 = {
        b"MQU2": ("QuoteUpdated", "quote", "Quote"),
        b"MTO2": ("TradeOccurred", "trade", "Trade"),
        b"MBV2": ("BarCompleted", "bar", "Bar"),
        b"MGU2": ("GreeksUpdated", "greeks", "Greeks"),
        b"MRU2": ("RateUpdated", "rate", "Rate"),
        b"MTU2": ("Ticker24hUpdated", "ticker_24h", "Ticker"),
        b"MMP2": ("MarkPriceUpdated", "mark_price", "MarkPrice"),
        b"MFD2": ("FundingRateUpdated", "funding_rate", "FundingRate"),
        b"MOI2": ("OpenInterestUpdated", "open_interest", "OpenInterest"),
        b"MIP2": ("IndexPriceUpdated", "index_price", "IndexPrice"),
        b"MOS2": ("OrderBookSnapshotReceived", "order_book_snapshot", "Snapshot"),
        b"MOD2": ("OrderBookDeltaReceived", "order_book_delta", "Delta"),
        b"MOR2": ("OrderBookResyncRequired", "order_book_resync", None),
    }.get(identifier)
    if v2 is not None:
        return _decode_v2_event(payload, *v2)
    raise ValueError(f"unsupported Market event identifier: {identifier!r}")


def _decode_v2_event(
    payload: bytes, root_name: str, kind: str, value_name: str | None
) -> MarketEventRecord:
    module = __import__(
        f"kairospy.infrastructure.protocol.generated.kairos.market.v2.{root_name}",
        fromlist=[root_name],
    )
    root_type = getattr(module, root_name)
    root = cast(Any, root_type.GetRootAs(payload, 0))
    metadata = cast(Any, root.Metadata())
    if metadata is None:
        raise ValueError(f"{root_name} metadata is missing")
    value = None if value_name is None else getattr(root, value_name)()
    if value_name is not None and value is None:
        raise ValueError(f"{root_name} payload is missing")
    if kind == "quote":
        value = _decode_quote(value)
    elif kind == "trade":
        value = _decode_trade(value)
    elif kind == "bar":
        value = _decode_bar(value)
    elif kind == "greeks":
        value = _decode_greeks(value)
    event_time = int(metadata.OccurredAtUnixNanos())
    occurred_at = (
        None
        if not event_time
        else datetime.fromtimestamp(event_time / 1_000_000_000, tz=timezone.utc)
    )
    return MarketEventRecord(
        stream_id=_required_header_text(metadata.StreamId(), "stream_id"),
        sequence=int(metadata.Sequence()),
        kind=kind,
        payload=value,
        occurred_at=occurred_at,
        schema_version=2,
        producer=_required_header_text(metadata.ProducerId(), "producer_id"),
        causation_id=_header_text(metadata.CausationId()),
        launch_id=_header_text(metadata.LaunchId()),
        instance_id=_header_text(metadata.InstanceId()),
    )


_decode_market_event = decode_market_event


def _required_header_text(value: bytes | None, name: str) -> str:
    result = _header_text(value)
    if result is None:
        raise ValueError(f"Market event {name} is required")
    return result


def _header_text(value: bytes | None) -> str | None:
    if value is None:
        return None
    result = value.decode()
    return result if result.strip() else None
