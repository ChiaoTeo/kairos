from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import mmap
from pathlib import Path
import struct
import sys
from typing import AsyncIterator

from kairospy.application.strategy.domain.messages import EventEnvelope, SnapshotEnvelope

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
            result = f"{digits[:-self.scale]}.{digits[-self.scale:]}"
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
class MarketDataView:
    quotes: tuple[QuoteView, ...]
    trades: tuple[TradeView, ...] = ()

    def current(self, instrument_id: str) -> QuoteView | None:
        return next((quote for quote in self.quotes if quote.instrument_id == instrument_id), None)


class MmapMarketSnapshotReader:
    """Read the Rust double-slot market.current snapshot without owning it."""

    _MAGIC = b"KSS1"
    _HEADER_SIZE = 64
    _SLOT_COUNT = 2

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read(self, view_key: str) -> SnapshotEnvelope:
        if view_key != "market.current":
            raise KeyError(view_key)
        with self.path.open("rb") as file:
            with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                return self._decode(self._active_payload(mapped))

    def _active_payload(self, mapped: mmap.mmap) -> bytes:
        if len(mapped) < self._HEADER_SIZE or mapped[:4] != self._MAGIC:
            raise ValueError("invalid shared snapshot header")
        version, slots, slot_size = struct.unpack_from("<HHI", mapped, 4)
        if version != 1 or slots != self._SLOT_COUNT or slot_size <= 0:
            raise ValueError("unsupported shared snapshot layout")
        if len(mapped) < self._HEADER_SIZE + slots * slot_size:
            raise ValueError("truncated shared snapshot file")
        for _ in range(8):
            active = mapped[12]
            if active >= slots:
                raise ValueError("invalid active snapshot slot")
            length = struct.unpack_from("<I", mapped, 24 + active * 4)[0]
            generation = struct.unpack_from("<Q", mapped, 32 + active * 8)[0]
            if not 0 < length <= slot_size:
                raise ValueError("active snapshot slot is empty or too large")
            start = self._HEADER_SIZE + active * slot_size
            payload = bytes(mapped[start:start + length])
            if active == mapped[12] and generation == struct.unpack_from("<Q", mapped, 32 + active * 8)[0]:
                return payload
        raise RuntimeError("shared snapshot changed while being read")

    @staticmethod
    def _decode(payload: bytes) -> SnapshotEnvelope:
        from kairospy.infrastructure.transport.generated.kairos.market.v1.MarketDataSnapshot import MarketDataSnapshot

        if payload[4:8] != b"PMC1":
            raise ValueError("invalid MarketDataSnapshot identifier")
        root = MarketDataSnapshot.GetRootAs(payload, 0)
        header = root.Header()
        data = root.Payload()
        if header is None or data is None:
            raise ValueError("market snapshot is missing header or payload")
        quotes = tuple(_decode_quote(data.Quotes(index)) for index in range(data.QuotesLength()))
        return SnapshotEnvelope(
            view_key=header.ViewKey().decode(),
            snapshot_id=header.SnapshotId().decode(),
            owner_actor_id=header.OwnerActorId().decode(),
            event_stream_id=header.EventStreamId().decode(),
            event_sequence=header.EventSequence(),
            generation=header.Generation(),
            payload=MarketDataView(quotes=quotes),
        )


def _decode_quote(value: object) -> QuoteView:
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
        event_time_unix_nanos=value.EventTimeUnixNanos(),
        source_id=text("SourceId"),
    )


class UnixMarketEventStream:
    """Consume length-prefixed QuoteMessage frames from kairos-market."""

    def __init__(self, socket_path: str | Path, *, stream_id: str = "market.events", replayable: bool = False) -> None:
        self.socket_path = Path(socket_path)
        self.stream_id = stream_id
        self.replayable = replayable

    def can_join(self, event_sequence: int) -> bool:
        return event_sequence == 0 or self.replayable

    async def events(self, after_sequence: int = 0) -> AsyncIterator[EventEnvelope]:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        try:
            while True:
                prefix = await reader.readexactly(4)
                (length,) = struct.unpack(">I", prefix)
                if length == 0 or length > 4 * 1024 * 1024:
                    raise ValueError("invalid market event frame length")
                payload = await reader.readexactly(length)
                event = _decode_market_event(payload)
                if event.sequence <= after_sequence:
                    continue
                yield event
        except asyncio.IncompleteReadError:
            return
        finally:
            writer.close()
            await writer.wait_closed()


def _decode_market_event(payload: bytes) -> EventEnvelope:
    if payload[4:8] == b"MTR1":
        return _decode_trade_event(payload)
    return _decode_quote_event(payload)


def _decode_quote_event(payload: bytes) -> EventEnvelope:
    from kairospy.infrastructure.transport.generated.kairos.market.v1.QuoteMessage import QuoteMessage

    if payload[4:8] != b"MQT1":
        raise ValueError("invalid QuoteMessage identifier")
    root = QuoteMessage.GetRootAs(payload, 0)
    header = root.Header()
    quote = root.Payload()
    if header is None or quote is None:
        raise ValueError("quote message is missing header or payload")
    event_time = header.EventTimeUnixNanos()
    occurred_at = None if not event_time else datetime.fromtimestamp(event_time / 1_000_000_000, tz=timezone.utc)
    return EventEnvelope(
        stream_id=header.StreamId().decode(),
        sequence=header.Sequence(),
        domain="data",
        kind="quote",
        payload=_decode_quote(quote),
        occurred_at=occurred_at,
    )


def _decode_trade_event(payload: bytes) -> EventEnvelope:
    from kairospy.infrastructure.transport.generated.kairos.market.v1.TradeMessage import TradeMessage

    root = TradeMessage.GetRootAs(payload, 0)
    header = root.Header()
    trade = root.Payload()
    if header is None or trade is None:
        raise ValueError("trade message is missing header or payload")

    def text(name: str) -> str | None:
        raw = getattr(trade, name)()
        return None if raw is None else raw.decode()

    def decimal(name: str) -> DecimalValue | None:
        raw = getattr(trade, name)()
        return None if raw is None else DecimalValue(raw.Mantissa(), raw.Scale())

    event_time = header.EventTimeUnixNanos()
    occurred_at = None if not event_time else datetime.fromtimestamp(event_time / 1_000_000_000, tz=timezone.utc)
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
