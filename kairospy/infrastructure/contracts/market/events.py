"""Market v2 event decoding into generated FlatBuffers roots."""

from __future__ import annotations

from importlib import import_module
import sys
from typing import Any


_generated_kairos = import_module("kairospy.infrastructure.protocol.generated.kairos")
sys.modules.setdefault("kairos", _generated_kairos)


_EVENT_ROOTS: tuple[tuple[bytes, str], ...] = (
    (b"MQU2", "QuoteUpdated"),
    (b"MTO2", "TradeOccurred"),
    (b"MBV2", "BarCompleted"),
    (b"MGU2", "GreeksUpdated"),
    (b"MRU2", "RateUpdated"),
    (b"MTU2", "Ticker24hUpdated"),
    (b"MMP2", "MarkPriceUpdated"),
    (b"MFR2", "FundingRateUpdated"),
    (b"MOI2", "OpenInterestUpdated"),
    (b"MIP2", "IndexPriceUpdated"),
    (b"MOS2", "OrderBookSnapshotReceived"),
    (b"MOD2", "OrderBookDeltaReceived"),
    (b"MOR2", "OrderBookResyncRequired"),
)


def decode_event(payload: bytes) -> Any:
    """Decode a Market v2 event and return its generated root object."""

    for identifier, root_name in _EVENT_ROOTS:
        if len(payload) < 8 or payload[4:8] != identifier:
            continue
        module = __import__(
            f"kairospy.infrastructure.protocol.generated.kairos.market.v2.{root_name}",
            fromlist=[root_name],
        )
        return getattr(module, root_name).GetRootAs(payload, 0)
    raise ValueError("unknown Market v2 event identifier")


__all__ = ["decode_event"]
