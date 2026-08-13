from __future__ import annotations

import asyncio
import flatbuffers
import os
import pytest
import struct
from pathlib import Path

from kairospy.infrastructure.contracts.base import MmapSnapshotReader
from kairospy.infrastructure.transport import (
    EventStreamGap,
    MmapMarketSnapshotReader,
    SharedSnapshotReader,
    UnixMarketEventStream,
)
from kairospy.infrastructure.transport.generated.kairos.common.v1 import (
    MessageHeader,
    SnapshotHeader,
)
from kairospy.infrastructure.transport.generated.kairos.market.v1 import (
    MarketData,
    MarketDataSnapshot,
    Quote,
    QuoteMessage,
)
from kairospy.infrastructure.transport.generated.kairos.market.v1.MarketDataSnapshot import (
    MarketDataSnapshot as MarketDataSnapshotTable,
)


def _empty_market_snapshot(view_key_value: str = "market.current") -> bytes:
    builder = flatbuffers.Builder(1024)

    def string(value: str) -> int:
        return builder.CreateString(value)

    snapshot_id = string("market:7")
    view_key = string(view_key_value)
    owner_actor_id = string("market-actor")
    event_stream_id = string("market.events")
    MarketData.MarketDataStart(builder)
    MarketData.MarketDataAddQuoteCount(builder, 0)
    payload = MarketData.MarketDataEnd(builder)
    SnapshotHeader.SnapshotHeaderStart(builder)
    SnapshotHeader.SnapshotHeaderAddSnapshotId(builder, snapshot_id)
    SnapshotHeader.SnapshotHeaderAddViewKey(builder, view_key)
    SnapshotHeader.SnapshotHeaderAddOwnerActorId(builder, owner_actor_id)
    SnapshotHeader.SnapshotHeaderAddEventStreamId(builder, event_stream_id)
    SnapshotHeader.SnapshotHeaderAddEventSequence(builder, 4)
    SnapshotHeader.SnapshotHeaderAddGeneration(builder, 7)
    header = SnapshotHeader.SnapshotHeaderEnd(builder)
    MarketDataSnapshot.MarketDataSnapshotStart(builder)
    MarketDataSnapshot.MarketDataSnapshotAddHeader(builder, header)
    MarketDataSnapshot.MarketDataSnapshotAddPayload(builder, payload)
    root = MarketDataSnapshot.MarketDataSnapshotEnd(builder)
    builder.Finish(root, file_identifier=b"PMC1")
    return bytes(builder.Output())


def _write_shared_snapshot(path: Path, payload: bytes) -> None:
    slot_size = 4096
    data = bytearray(64 + 2 * slot_size)
    data[:4] = b"KSS1"
    struct.pack_into("<HHI", data, 4, 1, 2, slot_size)
    data[64 : 64 + len(payload)] = payload
    struct.pack_into("<I", data, 24, len(payload))
    struct.pack_into("<Q", data, 32, 7)
    path.write_bytes(data)


def _quote_message(sequence: int = 1) -> bytes:
    builder = flatbuffers.Builder(1024)

    def string(value: str) -> int:
        return builder.CreateString(value)

    message_id = string("message-1")
    stream_id = string("market.events")
    producer_id = string("market-actor")
    instrument_id = string("BTCUSDT")
    market_id = string("market:btc")
    quote_source = string("binance")
    Quote.QuoteStart(builder)
    Quote.QuoteAddInstrumentId(builder, instrument_id)
    Quote.QuoteAddMarketId(builder, market_id)
    Quote.QuoteAddSourceId(builder, quote_source)
    quote = Quote.QuoteEnd(builder)
    MessageHeader.MessageHeaderStart(builder)
    MessageHeader.MessageHeaderAddMessageId(builder, message_id)
    MessageHeader.MessageHeaderAddStreamId(builder, stream_id)
    MessageHeader.MessageHeaderAddProducerId(builder, producer_id)
    MessageHeader.MessageHeaderAddSequence(builder, sequence)
    header = MessageHeader.MessageHeaderEnd(builder)
    QuoteMessage.QuoteMessageStart(builder)
    QuoteMessage.QuoteMessageAddHeader(builder, header)
    QuoteMessage.QuoteMessageAddPayload(builder, quote)
    root = QuoteMessage.QuoteMessageEnd(builder)
    builder.Finish(root, file_identifier=b"MQT1")
    return bytes(builder.Output())


def test_python_reads_rust_market_snapshot_contract(tmp_path: Path) -> None:
    path = tmp_path / "market.snapshot"
    _write_shared_snapshot(path, _empty_market_snapshot())

    snapshot = MmapMarketSnapshotReader(path).read("market.current")

    assert snapshot.snapshot_id == "market:7"
    assert snapshot.owner_actor_id == "market-actor"
    assert snapshot.event_stream_id == "market.events"
    assert snapshot.event_sequence == 4
    assert snapshot.generation == 7
    assert snapshot.quotes == ()
    assert MmapMarketSnapshotReader(path).read("market.current").event_sequence == 4

    generic = MmapSnapshotReader(
        path,
        file_identifier=b"PMC1",
        root_type=MarketDataSnapshotTable,
    ).read()
    assert generic.metadata.view_key == "market.current"
    assert generic.metadata.event_stream_id == "market.events"
    assert generic.event_sequence == 4


def test_python_reads_deterministic_market_view_path(tmp_path: Path) -> None:
    path = tmp_path / "market.snapshot"
    view_path = (
        tmp_path / "views" / "binance" / "market:btc" / "quote" / "current.snapshot"
    )
    view_path.parent.mkdir(parents=True)
    _write_shared_snapshot(
        view_path,
        _empty_market_snapshot("market.view.binance.market:btc.quote"),
    )

    snapshot = MmapMarketSnapshotReader(path).read(
        "market.view.binance.market:btc.quote"
    )

    assert snapshot.view_key == "market.view.binance.market:btc.quote"


def test_python_reads_qualified_market_view_path(tmp_path: Path) -> None:
    path = tmp_path / "market.snapshot"
    view_path = (
        tmp_path
        / "views"
        / "binance"
        / "market:btc"
        / "bar"
        / "1m"
        / "current.snapshot"
    )
    view_path.parent.mkdir(parents=True)
    _write_shared_snapshot(
        view_path,
        _empty_market_snapshot("market.view.binance.market:btc.bar.1m"),
    )

    snapshot = MmapMarketSnapshotReader(path).read(
        "market.view.binance.market:btc.bar.1m"
    )

    assert snapshot.view_key == "market.view.binance.market:btc.bar.1m"


def test_shared_snapshot_reader_returns_envelope_generation(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.bin"
    payload = b"payload"
    _write_shared_snapshot(path, payload)

    snapshot = SharedSnapshotReader(path).read()

    assert snapshot.generation == 7
    assert snapshot.payload == payload


def test_shared_snapshot_reader_rejects_invalid_magic(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.bin"
    path.write_bytes(b"bad")

    with pytest.raises(ValueError, match="invalid shared snapshot header"):
        SharedSnapshotReader(path).read()


def test_python_consumes_length_prefixed_market_quote(tmp_path: Path) -> None:
    async def scenario() -> None:
        socket = Path(f"/tmp/kairos-market-test-{os.getpid()}.sock")
        socket.unlink(missing_ok=True)
        payload = _quote_message()

        async def handler(reader, writer) -> None:
            writer.write(struct.pack(">I", len(payload)) + payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handler, path=str(socket))
        try:
            stream = UnixMarketEventStream(socket)
            assert stream.can_join(1)
            event = await anext(stream.events())
            assert event.sequence == 1
            assert event.kind == "quote"
            assert event.payload.instrument_id == "BTCUSDT"
        finally:
            server.close()
            await server.wait_closed()
            socket.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_python_market_stream_rejects_sequence_gaps(tmp_path: Path) -> None:
    async def scenario() -> None:
        socket = Path(f"/tmp/kairos-market-gap-{os.getpid()}.sock")
        socket.unlink(missing_ok=True)
        first = _quote_message(sequence=1)
        second = _quote_message(sequence=3)

        async def handler(reader, writer) -> None:
            for payload in (first, second):
                writer.write(struct.pack(">I", len(payload)) + payload)
                await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handler, path=str(socket))
        try:
            stream = UnixMarketEventStream(socket, reconnect_delay=0)
            events = stream.events()
            assert (await anext(events)).sequence == 1
            with pytest.raises(EventStreamGap, match="expected sequence 2"):
                await anext(events)
            await events.aclose()
        finally:
            server.close()
            await server.wait_closed()
            socket.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_python_replay_market_stream_finishes_at_eof() -> None:
    async def scenario() -> None:
        socket = Path(f"/tmp/kairos-market-replay-{os.getpid()}.sock")
        socket.unlink(missing_ok=True)
        payload = _quote_message(sequence=1)

        async def handler(reader, writer) -> None:
            writer.write(struct.pack(">I", len(payload)) + payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handler, path=str(socket))
        try:
            stream = UnixMarketEventStream(socket, replayable=True, reconnect_delay=0)
            events = stream.events()
            assert (await anext(events)).sequence == 1
            with pytest.raises(StopAsyncIteration):
                await anext(events)
        finally:
            server.close()
            await server.wait_closed()
            socket.unlink(missing_ok=True)

    asyncio.run(scenario())
