"""Risk v2 typed event decoding into generated FlatBuffers roots."""

from __future__ import annotations

from typing import Any
import sys

from kairospy.infrastructure.protocol.generated import kairos as _generated_kairos

sys.modules.setdefault("kairos", _generated_kairos)


_EVENT_ROOTS: tuple[tuple[bytes, str], ...] = (
    (b"RDV2", "RiskDecisionMade"),
    (b"RRV2", "ReservationReserved"),
    (b"RRC2", "ReservationConsumed"),
    (b"RRL2", "ReservationReleased"),
    (b"RRX2", "ReservationExpired"),
    (b"RKO2", "CircuitOpened"),
    (b"RKC2", "CircuitClosed"),
)


def decode_event(payload: bytes) -> Any:
    """Decode one Risk v2 event and return its generated root object."""

    for identifier, root_name in _EVENT_ROOTS:
        if len(payload) < 8 or payload[4:8] != identifier:
            continue
        module = __import__(
            f"kairospy.infrastructure.protocol.generated.kairos.risk.v2.{root_name}",
            fromlist=[root_name],
        )
        return getattr(module, root_name).GetRootAs(payload, 0)
    raise ValueError("unknown Risk v2 event identifier")


__all__ = ["decode_event"]
