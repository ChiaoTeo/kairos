"""Reference v2 typed event decoding into generated FlatBuffers roots."""

from __future__ import annotations

from typing import Any
import sys

from kairospy.infrastructure.transport.generated import kairos as _generated_kairos

sys.modules.setdefault("kairos", _generated_kairos)


_EVENT_ROOTS: tuple[tuple[bytes, str], ...] = (
    (b"RENU", "ExchangeUpserted"),
    (b"REND", "ExchangeUpdated"),
    (b"RAU2", "AssetUpserted"),
    (b"RAD2", "AssetUpdated"),
    (b"RIU2", "InstrumentUpserted"),
    (b"RID2", "InstrumentUpdated"),
    (b"RLU2", "ListingUpserted"),
    (b"RLD2", "ListingUpdated"),
    (b"RMU2", "MarketUpserted"),
    (b"RMD2", "MarketUpdated"),
)


def decode_event(payload: bytes) -> Any:
    """Decode one Reference v2 event into its generated root object."""

    for identifier, root_name in _EVENT_ROOTS:
        if len(payload) < 8 or payload[4:8] != identifier:
            continue
        module = __import__(
            f"kairospy.infrastructure.transport.generated.kairos.reference.v2.{root_name}",
            fromlist=[root_name],
        )
        return getattr(module, root_name).GetRootAs(payload, 0)
    raise ValueError("unknown Reference v2 event identifier")


__all__ = ["decode_event"]
