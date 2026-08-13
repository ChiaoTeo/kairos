from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import struct
import sys
from typing import Any, AsyncIterator, cast

from kairospy.application.market import (
    Bar,
    EventStreamGap,
    MarketSnapshot,
    OptionGreeks,
    Quote,
    Trade,
)
from kairospy.application.market.events import MarketEventRecord
from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import InstrumentId, MarketId, datetime_from_unix_nanos
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader
from kairospy.infrastructure.transport.aeron_bridge import check_aeron_bridge

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
class PriceLevelView:
    price: DecimalValue
    quantity: DecimalValue


@dataclass(frozen=True, slots=True)
class OrderBookView:
    market_id: str
    instrument_id: str
    source_id: str | None
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


class MmapMarketSnapshotReader:
    """Read a Market current view from the Rust double-slot snapshots.

    ``market.current`` remains the compatibility aggregate. New view keys use
    the deterministic layout ``views/<source>/<market>/<kind>/current.snapshot``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._reader = SharedSnapshotReader(self.path)

    def read(self, view_key: str) -> MarketSnapshot:
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
    def _decode(payload: bytes) -> MarketSnapshot:
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
        trades = tuple(
            _decode_trade(data.Trades(index)) for index in range(data.TradesLength())
        )
        bars = tuple(
            _decode_bar(data.Bars(index)) for index in range(data.BarsLength())
        )
        greeks = tuple(
            _decode_greeks(data.Greeks(index)) for index in range(data.GreeksLength())
        )
        return MarketSnapshot(
            view_key=cast(bytes, header.ViewKey()).decode(),
            snapshot_id=cast(bytes, header.SnapshotId()).decode(),
            owner_actor_id=cast(bytes, header.OwnerActorId()).decode(),
            generation=header.Generation(),
            quotes=tuple(_quote_model(value) for value in quotes),
            trades=tuple(_trade_model(value) for value in trades),
            bars=tuple(_bar_model(value) for value in bars),
            greeks=tuple(_greeks_model(value) for value in greeks),
        )


def _instrument(value: str) -> InstrumentRef:
    identifier = InstrumentId(value)
    return InstrumentRef(identifier, value.rsplit(":", 1)[-1])


def _market_id(value: str | None, kind: str) -> MarketId:
    if value is None or not value.strip():
        raise ValueError(f"{kind} market_id is required")
    return MarketId(value)


def _decimal(value: DecimalValue | None) -> Decimal | None:
    return None if value is None else Decimal(value.value)


def _quote_model(value: QuoteView) -> Quote:
    return Quote(
        market_id=_market_id(value.market_id, "quote"),
        instrument=_instrument(value.instrument_id),
        bid_price=_decimal(value.bid_price),
        bid_quantity=_decimal(value.bid_quantity),
        ask_price=_decimal(value.ask_price),
        ask_quantity=_decimal(value.ask_quantity),
        occurred_at=datetime_from_unix_nanos(value.event_time_unix_nanos),
        occurred_at_unix_nanos=value.event_time_unix_nanos,
        source_id=value.source_id,
    )


def _trade_model(value: TradeView) -> Trade:
    if value.price is None or value.quantity is None:
        raise ValueError("trade price and quantity are required")
    return Trade(
        market_id=_market_id(value.market_id, "trade"),
        instrument=_instrument(value.instrument_id),
        price=Decimal(value.price.value),
        quantity=Decimal(value.quantity.value),
        aggressor_side=None,
        occurred_at=datetime_from_unix_nanos(value.event_time_unix_nanos),
        occurred_at_unix_nanos=value.event_time_unix_nanos,
        source_id=value.source_id,
    )


def _bar_model(value: BarView) -> Bar:
    return Bar(
        market_id=_market_id(value.market_id, "bar"),
        instrument=_instrument(value.instrument_id),
        timeframe=value.timeframe,
        open=Decimal(value.open.value),
        high=Decimal(value.high.value),
        low=Decimal(value.low.value),
        close=Decimal(value.close.value),
        volume=_decimal(value.volume),
        occurred_at=datetime_from_unix_nanos(value.event_time_unix_nanos),
        occurred_at_unix_nanos=value.event_time_unix_nanos,
        source_id=value.source_id,
    )


def _greeks_model(value: GreeksView) -> OptionGreeks:
    return OptionGreeks(
        market_id=_market_id(value.market_id, "greeks"),
        instrument=_instrument(value.instrument_id),
        expiry_unix_nanos=value.expiry_unix_nanos,
        strike=_decimal(value.strike),
        delta=_decimal(value.delta),
        gamma=_decimal(value.gamma),
        vega=_decimal(value.vega),
        theta=_decimal(value.theta),
        implied_volatility=_decimal(value.implied_volatility),
        occurred_at=datetime_from_unix_nanos(value.event_time_unix_nanos),
        occurred_at_unix_nanos=value.event_time_unix_nanos,
        source_id=value.source_id,
        derivation=value.derivation,
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
        market_id=text("MarketId"),
        trade_id=text("TradeId"),
        price=decimal("Price"),
        quantity=decimal("Quantity"),
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
    """Consume the replay Market frame stream.

    Production Market events use Aeron.  This adapter remains only for the
    launch-owned replay process and never reads or recovers through mmap.
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

    async def events(self, after_sequence: int = 0) -> AsyncIterator[MarketEventRecord]:
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


class AeronMarketEventSource:
    """Market-owned adapter over the native Aeron subscription bridge."""

    replayable = False
    join_from_latest = True

    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = "aeron:udp?endpoint=localhost:40123",
        stream_id: int = 1301,
        binary: str,
    ) -> None:
        self.aeron_dir = None if aeron_dir is None else str(aeron_dir)
        self.channel = channel
        self.stream_id = stream_id
        self.binary = binary

    def _command(self) -> list[str]:
        command = [
            self.binary,
            "--aeron-channel",
            self.channel,
            "--stream-id",
            str(self.stream_id),
        ]
        if self.aeron_dir is not None:
            command.extend(("--aeron-dir", self.aeron_dir))
        return command

    def check_ready(self) -> None:
        check_aeron_bridge(self._command(), domain="Market")

    async def events(self, after_sequence: int = 0) -> AsyncIterator[MarketEventRecord]:
        process = await asyncio.create_subprocess_exec(
            *self._command(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        try:
            while True:
                try:
                    size = struct.unpack(">I", await process.stdout.readexactly(4))[0]
                    if size == 0 or size > 4 * 1024 * 1024:
                        raise ValueError("invalid Market Aeron frame length")
                    payload = await process.stdout.readexactly(size)
                except asyncio.IncompleteReadError:
                    break
                record = decode_market_event(payload)
                if record.sequence > after_sequence:
                    yield record
            status = await process.wait()
            if status != 0:
                assert process.stderr is not None
                error = (await process.stderr.read()).decode(errors="replace").strip()
                raise RuntimeError(error or f"Market Aeron bridge exited with {status}")
            raise RuntimeError("Market Aeron bridge ended unexpectedly")
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()


def decode_market_event(payload: bytes) -> MarketEventRecord:
    identifier = payload[4:8]
    if identifier == b"MOB1":
        return _decode_orderbook_event(payload)
    if identifier == b"MTR1":
        return _decode_trade_event(payload)
    if identifier == b"MBA1":
        return _decode_bar_event(payload)
    if identifier == b"MGR1":
        return _decode_greeks_event(payload)
    if identifier == b"MQT1":
        return _decode_quote_event(payload)
    ignored = {
        b"MRA1": ("RateMessage", "rate"),
        b"MT24": ("Ticker24hMessage", "ticker_24h"),
        b"MMP1": ("MarkPriceMessage", "mark_price"),
        b"MIP1": ("IndexPriceMessage", "index_price"),
        b"MFR1": ("FundingRateMessage", "funding_rate"),
        b"MOI1": ("OpenInterestMessage", "open_interest"),
        b"MIS1": ("InstrumentStatusMessage", "instrument_status"),
    }.get(identifier)
    if ignored is not None:
        return _decode_non_strategy_event(payload, *ignored)
    raise ValueError(f"unsupported Market event identifier: {identifier!r}")


_decode_market_event = decode_market_event


def _decode_non_strategy_event(
    payload: bytes, message_name: str, kind: str
) -> MarketEventRecord:
    module = __import__(
        f"kairospy.infrastructure.transport.generated.kairos.market.v1.{message_name}",
        fromlist=[message_name],
    )
    root = cast(Any, getattr(module, message_name).GetRootAs(payload, 0))
    header = cast(Any, root.Header())
    if header is None:
        raise ValueError(f"{message_name} header is missing")
    event_time = int(header.EventTimeUnixNanos())
    occurred_at = (
        None
        if not event_time
        else datetime.fromtimestamp(event_time / 1_000_000_000, tz=timezone.utc)
    )
    return MarketEventRecord(
        stream_id=_required_header_text(header.StreamId(), "stream_id"),
        sequence=int(header.Sequence()),
        kind=kind,
        payload=None,
        occurred_at=occurred_at,
        producer=_required_header_text(header.ProducerId(), "producer_id"),
        launch_id=_header_text(header.LaunchId()),
        instance_id=_header_text(header.InstanceId()),
    )


def _required_header_text(value: bytes | None, name: str) -> str:
    result = _header_text(value)
    if result is None:
        raise ValueError(f"Market event {name} is required")
    return result


def _decode_orderbook_event(payload: bytes) -> MarketEventRecord:
    from kairospy.infrastructure.transport.generated.kairos.market.v1.OrderBookMessage import (
        OrderBookMessage,
    )

    root = OrderBookMessage.GetRootAs(payload, 0)
    header = cast(Any, root.Header())
    book = cast(Any, root.Payload())
    if header is None or book is None:
        raise ValueError("order-book message is missing header or payload")

    def text(raw: bytes | None) -> str | None:
        return None if raw is None else raw.decode()

    def level(side: str, index: int) -> PriceLevelView:
        value = cast(Any, getattr(book, side)(index))
        if value is None:
            raise ValueError("order-book message contains an empty level")
        price = cast(Any, value.Price())
        quantity = cast(Any, value.Quantity())
        return PriceLevelView(
            DecimalValue(price.Mantissa(), price.Scale()),
            DecimalValue(quantity.Mantissa(), quantity.Scale()),
        )

    event_time = header.EventTimeUnixNanos()
    occurred_at = (
        None
        if not event_time
        else datetime.fromtimestamp(event_time / 1_000_000_000, tz=timezone.utc)
    )
    return MarketEventRecord(
        stream_id=header.StreamId().decode(),
        sequence=header.Sequence(),
        kind="orderbook",
        payload=OrderBookView(
            market_id=text(book.MarketId()) or "",
            instrument_id=text(book.InstrumentId()) or "",
            source_id=text(book.SourceId()),
            sequence=book.Sequence(),
            first_sequence=book.FirstSequence(),
            last_sequence=book.LastSequence(),
            event_time_unix_nanos=book.EventTimeUnixNanos(),
            synchronized=book.Synchronized(),
            depth_policy=text(book.DepthPolicy()),
            checksum=text(book.Checksum()),
            bids=tuple(level("Bids", index) for index in range(book.BidsLength())),
            asks=tuple(level("Asks", index) for index in range(book.AsksLength())),
        ),
        occurred_at=occurred_at,
        producer=_header_text(header.ProducerId()) or "market",
        launch_id=_header_text(header.LaunchId()),
        instance_id=_header_text(header.InstanceId()),
    )


def _decode_quote_event(payload: bytes) -> MarketEventRecord:
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
    return MarketEventRecord(
        stream_id=header.StreamId().decode(),
        sequence=header.Sequence(),
        kind="quote",
        payload=_decode_quote(quote),
        occurred_at=occurred_at,
        producer=_header_text(header.ProducerId()) or "market",
        launch_id=_header_text(header.LaunchId()),
        instance_id=_header_text(header.InstanceId()),
    )


def _decode_trade_event(payload: bytes) -> MarketEventRecord:
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
    return MarketEventRecord(
        stream_id=header.StreamId().decode(),
        sequence=header.Sequence(),
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
        producer=_header_text(header.ProducerId()) or "market",
        launch_id=_header_text(header.LaunchId()),
        instance_id=_header_text(header.InstanceId()),
    )


def _decode_bar_event(payload: bytes) -> MarketEventRecord:
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
    return MarketEventRecord(
        stream_id=header.StreamId().decode(),
        sequence=header.Sequence(),
        kind="bar",
        payload=_decode_bar(bar),
        occurred_at=occurred_at,
        producer=_header_text(header.ProducerId()) or "market",
        launch_id=_header_text(header.LaunchId()),
        instance_id=_header_text(header.InstanceId()),
    )


def _decode_greeks_event(payload: bytes) -> MarketEventRecord:
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
    return MarketEventRecord(
        stream_id=header.StreamId().decode(),
        sequence=header.Sequence(),
        kind="greeks",
        payload=_decode_greeks(greeks),
        occurred_at=occurred_at,
        producer=_header_text(header.ProducerId()) or "market",
        launch_id=_header_text(header.LaunchId()),
        instance_id=_header_text(header.InstanceId()),
    )


def _header_text(value: bytes | None) -> str | None:
    if value is None:
        return None
    result = value.decode()
    return result if result.strip() else None
