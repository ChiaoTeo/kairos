from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import struct
import sys
from typing import Any, AsyncIterator, cast

from kairospy.application.strategy.domain.messages import (
    EventEnvelope,
    SnapshotEnvelope,
)
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader

# The generated FlatBuffers modules use their schema namespace (``kairos``)
# for sibling imports. Keep that generated namespace private to this adapter
# while making those imports resolvable; application code never sees it.
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos

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
class QuoteView:
    instrument_id: str
    market_id: str | None
    bid_price: DecimalValue | None
    bid_quantity: DecimalValue | None
    ask_price: DecimalValue | None
    ask_quantity: DecimalValue | None
    event_time_unix_nanos: int
    source_id: str | None


@dataclass(frozen=True, slots=True)
class TradeView:
    instrument_id: str
    market_id: str | None
    trade_id: str | None
    price: DecimalValue | None
    quantity: DecimalValue | None
    event_time_unix_nanos: int
    source_id: str | None


@dataclass(frozen=True, slots=True)
class BarView:
    instrument_id: str
    market_id: str | None
    timeframe: str
    open: DecimalValue
    high: DecimalValue
    low: DecimalValue
    close: DecimalValue
    volume: DecimalValue | None
    event_time_unix_nanos: int
    source_id: str | None
    derivation: str | None


@dataclass(frozen=True, slots=True)
class GreeksView:
    instrument_id: str
    market_id: str | None
    expiry_unix_nanos: int
    strike: DecimalValue | None
    delta: DecimalValue | None
    gamma: DecimalValue | None
    vega: DecimalValue | None
    theta: DecimalValue | None
    implied_volatility: DecimalValue | None
    event_time_unix_nanos: int
    source_id: str | None
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


class EventStreamGap(RuntimeError):
    """Raised when a live stream skips a sequence and needs snapshot recovery."""

    def __init__(self, stream_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"event stream {stream_id} gap: expected sequence {expected}, received {actual}"
        )
        self.stream_id = stream_id
        self.expected = expected
        self.actual = actual


class MmapMarketSnapshotReader:
    """Read a Market current view from the Rust double-slot snapshots.

    ``market.current`` remains the compatibility aggregate. New view keys use
    the deterministic layout ``views/<source>/<market>/<kind>/current.snapshot``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._reader = SharedSnapshotReader(self.path)

    def read(self, view_key: str) -> SnapshotEnvelope:
        if view_key == "market.current":
            return self._decode(self._reader.read().payload)
        prefix = "market.view."
        if not view_key.startswith(prefix):
            raise KeyError(view_key)
        parts = view_key[len(prefix) :].split(".", 3)
        if len(parts) not in (3, 4) or not all(parts):
            raise KeyError(view_key)
        source_id, market_id, kind, *qualifier = parts
        path = self.path.parent / "views" / source_id / market_id / kind
        if qualifier:
            path /= qualifier[0]
        path /= "current.snapshot"
        return self._decode(SharedSnapshotReader(path).read().payload)

    @staticmethod
    def _decode(payload: bytes) -> SnapshotEnvelope:
        from kairospy.infrastructure.transport.generated.kairos.market.v1.MarketDataSnapshot import (
            MarketDataSnapshot,
        )

        if payload[4:8] != b"PMC1":
            raise ValueError("invalid MarketDataSnapshot identifier")
        root = MarketDataSnapshot.GetRootAs(payload, 0)
        header = cast(Any, root.Header())
        data = cast(Any, root.Payload())
        if header is None or data is None:
            raise ValueError("market snapshot is missing header or payload")
        quotes = tuple(
            _decode_quote(data.Quotes(index)) for index in range(data.QuotesLength())
        )
        bars = tuple(
            _decode_bar(data.Bars(index)) for index in range(data.BarsLength())
        )
        greeks = tuple(
            _decode_greeks(data.Greeks(index)) for index in range(data.GreeksLength())
        )
        return SnapshotEnvelope(
            view_key=cast(bytes, header.ViewKey()).decode(),
            snapshot_id=cast(bytes, header.SnapshotId()).decode(),
            owner_actor_id=cast(bytes, header.OwnerActorId()).decode(),
            event_stream_id=cast(bytes, header.EventStreamId()).decode(),
            event_sequence=header.EventSequence(),
            generation=header.Generation(),
            payload=MarketDataView(quotes=quotes, bars=bars, greeks=greeks),
        )


def _decode_quote(value: object) -> QuoteView:
    value = cast(Any, value)

    def text(name: str) -> str | None:
        raw = getattr(value, name)()
        return None if raw is None else raw.decode()

    def decimal(name: str) -> DecimalValue | None:
        raw = getattr(value, name)()
        return None if raw is None else DecimalValue(raw.Mantissa(), raw.Scale())

    return QuoteView(
        instrument_id=text("InstrumentId") or "",
        market_id=text("MarketId"),
        bid_price=decimal("BidPrice"),
        bid_quantity=decimal("BidQuantity"),
        ask_price=decimal("AskPrice"),
        ask_quantity=decimal("AskQuantity"),
        event_time_unix_nanos=getattr(value, "EventTimeUnixNanos")(),
        source_id=text("SourceId"),
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
        market_id=text("MarketId"),
        timeframe=text("Timeframe") or "",
        open=decimal("Open"),
        high=decimal("High"),
        low=decimal("Low"),
        close=decimal("Close"),
        volume=optional_decimal("Volume"),
        event_time_unix_nanos=getattr(value, "EventTimeUnixNanos")(),
        source_id=text("SourceId"),
        derivation=text("Derivation"),
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
        market_id=text("MarketId"),
        expiry_unix_nanos=getattr(value, "ExpiryUnixNanos")(),
        strike=decimal("Strike"),
        delta=decimal("Delta"),
        gamma=decimal("Gamma"),
        vega=decimal("Vega"),
        theta=decimal("Theta"),
        implied_volatility=decimal("ImpliedVolatility"),
        event_time_unix_nanos=getattr(value, "EventTimeUnixNanos")(),
        source_id=text("SourceId"),
        derivation=text("Derivation"),
    )


class UnixMarketEventStream:
    """Consume live Market frames with reconnect and sequence validation.

    The current Unix socket is a live-only change plane.  It has no replay
    handshake, so a disconnect or gap is surfaced to the host, which must
    re-read the contract snapshot before resuming.
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

    def can_join(self, event_sequence: int) -> bool:
        # Snapshot recovery establishes a new join point for the live-only
        # stream.  ``replayable`` remains available for future transports.
        return event_sequence >= 0 or self.replayable

    async def events(self, after_sequence: int = 0) -> AsyncIterator[EventEnvelope]:
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
                    expected = cursor + 1
                    if event.sequence != expected:
                        raise EventStreamGap(self.stream_id, expected, event.sequence)
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


def _decode_market_event(payload: bytes) -> EventEnvelope:
    if payload[4:8] == b"MTR1":
        return _decode_trade_event(payload)
    if payload[4:8] == b"MBA1":
        return _decode_bar_event(payload)
    if payload[4:8] == b"MGR1":
        return _decode_greeks_event(payload)
    return _decode_quote_event(payload)


def _decode_quote_event(payload: bytes) -> EventEnvelope:
    from kairospy.infrastructure.transport.generated.kairos.market.v1.QuoteMessage import (
        QuoteMessage,
    )

    if payload[4:8] != b"MQT1":
        raise ValueError("invalid QuoteMessage identifier")
    root = QuoteMessage.GetRootAs(payload, 0)
    header = cast(Any, root.Header())
    quote = cast(Any, root.Payload())
    if header is None or quote is None:
        raise ValueError("quote message is missing header or payload")
    event_time = header.EventTimeUnixNanos()
    occurred_at = (
        None
        if not event_time
        else datetime.fromtimestamp(event_time / 1_000_000_000, tz=timezone.utc)
    )
    return EventEnvelope(
        stream_id=header.StreamId().decode(),
        sequence=header.Sequence(),
        domain="data",
        kind="quote",
        payload=_decode_quote(quote),
        occurred_at=occurred_at,
    )


def _decode_trade_event(payload: bytes) -> EventEnvelope:
    from kairospy.infrastructure.transport.generated.kairos.market.v1.TradeMessage import (
        TradeMessage,
    )

    root = TradeMessage.GetRootAs(payload, 0)
    header = cast(Any, root.Header())
    trade = cast(Any, root.Payload())
    if header is None or trade is None:
        raise ValueError("trade message is missing header or payload")

    def text(name: str) -> str | None:
        raw = getattr(trade, name)()
        return None if raw is None else raw.decode()

    def decimal(name: str) -> DecimalValue | None:
        raw = getattr(trade, name)()
        return None if raw is None else DecimalValue(raw.Mantissa(), raw.Scale())

    event_time = header.EventTimeUnixNanos()
    occurred_at = (
        None
        if not event_time
        else datetime.fromtimestamp(event_time / 1_000_000_000, tz=timezone.utc)
    )
    return EventEnvelope(
        stream_id=header.StreamId().decode(),
        sequence=header.Sequence(),
        domain="data",
        kind="trade",
        payload=TradeView(
            instrument_id=text("InstrumentId") or "",
            market_id=text("MarketId"),
            trade_id=text("TradeId"),
            price=decimal("Price"),
            quantity=decimal("Quantity"),
            event_time_unix_nanos=trade.EventTimeUnixNanos(),
            source_id=text("SourceId"),
        ),
        occurred_at=occurred_at,
    )


def _decode_bar_event(payload: bytes) -> EventEnvelope:
    from kairospy.infrastructure.transport.generated.kairos.market.v1.BarMessage import (
        BarMessage,
    )

    root = BarMessage.GetRootAs(payload, 0)
    header = cast(Any, root.Header())
    bar = cast(Any, root.Payload())
    if header is None or bar is None:
        raise ValueError("bar message is missing header or payload")
    event_time = header.EventTimeUnixNanos()
    occurred_at = (
        None
        if not event_time
        else datetime.fromtimestamp(event_time / 1_000_000_000, tz=timezone.utc)
    )
    return EventEnvelope(
        stream_id=header.StreamId().decode(),
        sequence=header.Sequence(),
        domain="data",
        kind="bar",
        payload=_decode_bar(bar),
        occurred_at=occurred_at,
    )


def _decode_greeks_event(payload: bytes) -> EventEnvelope:
    from kairospy.infrastructure.transport.generated.kairos.market.v1.GreeksMessage import (
        GreeksMessage,
    )

    root = GreeksMessage.GetRootAs(payload, 0)
    header = cast(Any, root.Header())
    greeks = cast(Any, root.Payload())
    if header is None or greeks is None:
        raise ValueError("greeks message is missing header or payload")
    event_time = header.EventTimeUnixNanos()
    occurred_at = (
        None
        if not event_time
        else datetime.fromtimestamp(event_time / 1_000_000_000, tz=timezone.utc)
    )
    return EventEnvelope(
        stream_id=header.StreamId().decode(),
        sequence=header.Sequence(),
        domain="data",
        kind="greeks",
        payload=_decode_greeks(greeks),
        occurred_at=occurred_at,
    )
