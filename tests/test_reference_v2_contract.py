from __future__ import annotations

import flatbuffers

from kairospy.infrastructure.contracts.reference.events import decode_event
from kairospy.infrastructure.transport.reference import decode_reference_event
from kairospy.infrastructure.transport.generated.kairos.common.v2.EventMetadata import (
    EventMetadataAddEventId,
    EventMetadataAddOccurredAtUnixNanos,
    EventMetadataAddProducerId,
    EventMetadataAddSequence,
    EventMetadataAddStreamId,
    EventMetadataAddWorkspaceId,
    EventMetadataEnd,
    EventMetadataStart,
)
from kairospy.infrastructure.transport.generated.kairos.reference.v2.Market import (
    MarketAddExchangeId,
    MarketAddInstrumentId,
    MarketAddListingId,
    MarketAddMarketId,
    MarketAddInstrumentKind,
    MarketAddVenueSymbol,
    MarketEnd,
    MarketStart,
)
from kairospy.infrastructure.transport.generated.kairos.reference.v2.MarketUpserted import (
    MarketUpsertedAddCatalogRevision,
    MarketUpsertedAddMarket,
    MarketUpsertedAddMetadata,
    MarketUpsertedEnd,
    MarketUpsertedStart,
)


def _market_upserted_payload() -> bytes:
    builder = flatbuffers.Builder(1024)
    event_id = builder.CreateString("reference:event:1")
    stream_id = builder.CreateString("reference.events")
    producer_id = builder.CreateString("reference-actor")
    workspace_id = builder.CreateString("workspace:test")
    EventMetadataStart(builder)
    EventMetadataAddEventId(builder, event_id)
    EventMetadataAddStreamId(builder, stream_id)
    EventMetadataAddSequence(builder, 1)
    EventMetadataAddProducerId(builder, producer_id)
    EventMetadataAddWorkspaceId(builder, workspace_id)
    EventMetadataAddOccurredAtUnixNanos(builder, 10)
    metadata = EventMetadataEnd(builder)

    market_id = builder.CreateString("market:binance:spot:BTCUSDT")
    instrument_id = builder.CreateString("instrument:spot:BTC")
    listing_id = builder.CreateString("listing:binance:spot:BTCUSDT")
    exchange_id = builder.CreateString("exchange:binance")
    instrument_kind = builder.CreateString("spot")
    venue_symbol = builder.CreateString("BTCUSDT")
    MarketStart(builder)
    MarketAddMarketId(builder, market_id)
    MarketAddInstrumentId(builder, instrument_id)
    MarketAddListingId(builder, listing_id)
    MarketAddExchangeId(builder, exchange_id)
    MarketAddInstrumentKind(builder, instrument_kind)
    MarketAddVenueSymbol(builder, venue_symbol)
    market = MarketEnd(builder)

    MarketUpsertedStart(builder)
    MarketUpsertedAddMetadata(builder, metadata)
    MarketUpsertedAddCatalogRevision(builder, 3)
    MarketUpsertedAddMarket(builder, market)
    root = MarketUpsertedEnd(builder)
    builder.Finish(root, file_identifier=b"RMU2")
    return bytes(builder.Output())


def test_reference_v2_event_decoder_returns_generated_root() -> None:
    event = decode_event(_market_upserted_payload())

    assert event.CatalogRevision() == 3
    assert event.Metadata().Sequence() == 1
    assert event.Market().MarketId() == b"market:binance:spot:BTCUSDT"
    record = decode_reference_event(_market_upserted_payload())
    assert record.kind == "market_upserted"
    assert record.event_id == "reference:event:1"


def test_reference_v2_event_decoder_rejects_unknown_identifier() -> None:
    try:
        decode_event(b"invalid")
    except ValueError as error:
        assert str(error) == "unknown Reference v2 event identifier"
    else:
        raise AssertionError("unknown Reference event was accepted")
