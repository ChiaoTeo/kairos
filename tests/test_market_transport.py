from __future__ import annotations

import asyncio
import flatbuffers
import os
import pytest
import struct
from pathlib import Path
from types import SimpleNamespace

from kairospy.application.market.application import MarketApplication
from kairospy.application.market.events import MarketEventRecord
from kairospy.domain_types import MarketId
from kairospy.infrastructure.contracts.base import MmapSnapshotReader
from kairospy.infrastructure.transport import (
    EventStreamGap,
    MmapMarketSnapshotReader,
    SharedSnapshotReader,
    UnixMarketEventStream,
)
from kairospy.infrastructure.transport.market import decode_market_event
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
    MarketData.MarketDataStart(builder)
    MarketData.MarketDataAddQuoteCount(builder, 0)
    payload = MarketData.MarketDataEnd(builder)
    SnapshotHeader.SnapshotHeaderStart(builder)
    SnapshotHeader.SnapshotHeaderAddSnapshotId(builder, snapshot_id)
    SnapshotHeader.SnapshotHeaderAddViewKey(builder, view_key)
    SnapshotHeader.SnapshotHeaderAddOwnerActorId(builder, owner_actor_id)
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


def _quote_message(sequence: int = 1, market_id_value: str = "market:btc") -> bytes:
    builder = flatbuffers.Builder(1024)

    def string(value: str) -> int:
        return builder.CreateString(value)

    message_id = string("message-1")
    stream_id = string("market.events")
    producer_id = string("market-actor")
    instrument_id = string("BTCUSDT")
    market_id = string(market_id_value)
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
    assert snapshot.generation == 7
    assert snapshot.quotes == ()
    assert not hasattr(snapshot, "event_stream_id")
    assert not hasattr(snapshot, "event_sequence")

    generic = MmapSnapshotReader(
        path,
        file_identifier=b"PMC1",
        root_type=MarketDataSnapshotTable,
    ).read()
    assert generic.metadata.view_key == "market.current"
    assert generic.metadata.generation == 7
    assert not hasattr(generic.metadata, "event_stream_id")
    assert not hasattr(generic.metadata, "event_sequence")


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
            event = await anext(stream.events())
            assert event.sequence == 1
            assert event.kind == "quote"
            assert event.payload.instrument_id == "BTCUSDT"
        finally:
            server.close()
            await server.wait_closed()
            socket.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_market_event_decoder_rejects_unknown_file_identifier() -> None:
    payload = bytearray(_quote_message())
    payload[4:8] = b"BAD1"

    with pytest.raises(ValueError, match="unsupported Market event identifier"):
        decode_market_event(bytes(payload))


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
            application = MarketApplication(
                None,
                None,
                UnixMarketEventStream(socket, replayable=True, reconnect_delay=0),
                strategy_id="strategy",
                instance_id="instance",
            )
            events = application.events()
            assert (await anext(events)).metadata.sequence == 1
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


def test_live_market_source_joins_latest_then_enforces_continuity() -> None:
    class LiveRecords:
        join_from_latest = True

        def __init__(self, *sequences: int) -> None:
            self.records = tuple(
                decode_market_event(_quote_message(sequence=value))
                for value in sequences
            )

        async def events(self, after_sequence: int = 0):
            for record in self.records:
                if record.sequence > after_sequence:
                    yield record

    async def collect(source):
        application = MarketApplication(
            None,
            None,
            source,
            strategy_id="strategy",
            instance_id="instance",
        )
        return [event async for event in application.events()]

    assert len(asyncio.run(collect(LiveRecords(40, 41, 40, 42)))) == 3
    with pytest.raises(EventStreamGap, match="expected sequence 41"):
        asyncio.run(collect(LiveRecords(40, 42)))


def test_market_application_advances_over_non_strategy_observations() -> None:
    class Records:
        join_from_latest = False

        async def events(self, after_sequence: int = 0):
            yield MarketEventRecord("market.events", 1, "funding_rate", None)
            yield decode_market_event(_quote_message(sequence=2))

    application = MarketApplication(
        None,
        None,
        Records(),
        strategy_id="strategy",
        instance_id="instance",
    )

    events = asyncio.run(_collect_market_events(application))

    assert [event.metadata.sequence for event in events] == [2]


def test_live_market_events_are_filtered_by_strategy_subscription() -> None:
    class Commands:
        def subscribe(self, _request, **kwargs):
            return SimpleNamespace(
                request_id=kwargs["request_id"],
                status="accepted",
                result={"subscription_id": "subscription:btc-quotes"},
                error=None,
            )

    class Records:
        join_from_latest = False
        replayable = False

        async def events(self, after_sequence: int = 0):
            yield decode_market_event(_quote_message(1, "market:btc"))
            yield decode_market_event(_quote_message(2, "market:eth"))

    application = MarketApplication(
        Commands(),
        None,
        Records(),
        strategy_id="strategy",
        instance_id="instance",
    )
    application.subscribe_quotes(MarketId("market:btc"))

    events = asyncio.run(_collect_market_events(application))

    assert [str(event.data.market_id) for event in events] == ["market:btc"]
    assert application._event_cursor == 2


def test_replay_and_live_decoder_expose_the_same_public_market_event() -> None:
    raw = decode_market_event(_quote_message(1, "market:btc"))

    class LiveRecords:
        join_from_latest = False
        replayable = False

        async def events(self, after_sequence: int = 0):
            yield raw

    live = MarketApplication(
        None,
        None,
        LiveRecords(),
        strategy_id="strategy",
        instance_id="instance",
    )
    live_event = asyncio.run(_collect_market_events(live))[0]

    class ReplayRecords:
        join_from_latest = False
        replayable = True

        async def events(self, after_sequence: int = 0):
            yield live_event

    replay = MarketApplication(
        None,
        None,
        ReplayRecords(),
        strategy_id="strategy",
        instance_id="instance",
    )
    replay_event = asyncio.run(_collect_market_events(replay))[0]

    assert replay_event == live_event
    assert replay_event.metadata.stream_id == "market.events"
    assert replay_event.metadata.sequence == 1


async def _collect_market_events(application: MarketApplication):
    return [event async for event in application.events()]
