from __future__ import annotations

from base64 import b64decode

from kairospy.infrastructure.contracts.reference.events import decode_event
from kairospy.infrastructure.contracts.reference.source import decode_reference_event


MARKET_UPSERTED = b64decode(
    "GAAAAFJNVTIAAAAAAAAKABQAEAAIAAQACgAAACAAAAADAAAAAAAAANQAAAAQABwAGAAUABAADAAIAAQAEAAAABgAAAAgAAAAKAAAADwAAABcAAAAcAAAAAcAAABCVENVU0RUAAQAAABzcG90AAAAABAAAABleGNoYW5nZTpiaW5hbmNlAAAAABwAAABsaXN0aW5nOmJpbmFuY2U6c3BvdDpCVENVU0RUAAAAABMAAABpbnN0cnVtZW50OnNwb3Q6QlRDABsAAABtYXJrZXQ6YmluYW5jZTpzcG90OkJUQ1VTRFQAGAAkACAAHAAUABAADAAAAAAAAAAAAAQAGAAAAAoAAAAAAAAAGAAAACgAAAABAAAAAAAAADAAAABEAAAADgAAAHdvcmtzcGFjZTp0ZXN0AAAPAAAAcmVmZXJlbmNlLWFjdG9yABAAAAByZWZlcmVuY2UuZXZlbnRzAAAAABEAAAByZWZlcmVuY2U6ZXZlbnQ6MQAAAA=="
)


def test_reference_v2_event_decoder_returns_owner_native_event() -> None:
    event = decode_event(MARKET_UPSERTED)

    assert event.catalog_revision == 3
    assert event.metadata.sequence == 1
    assert event.payload.market_id == "market:binance:spot:BTCUSDT"
    record = decode_reference_event(MARKET_UPSERTED)
    assert record.kind == "market_upserted"
    assert record.event_id == "reference:event:1"


def test_reference_v2_event_decoder_rejects_unknown_identifier() -> None:
    try:
        decode_event(b"invalid")
    except ValueError as error:
        assert "unknown Reference v2 event identifier" in str(error)
    else:
        raise AssertionError("unknown Reference event was accepted")
