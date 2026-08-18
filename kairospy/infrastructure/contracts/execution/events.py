"""Execution v2 event-root decoding."""

from __future__ import annotations

from typing import Any
import sys

from kairospy.infrastructure.transport.generated import kairos as _generated_kairos

sys.modules.setdefault("kairos", _generated_kairos)

_EVENT_ROOTS: dict[bytes, str] = {
    b"EIA2": "IntentAccepted",
    b"EIR2": "IntentRejected",
    b"EIL2": "IntentLifecycleChanged",
    b"EPV2": "PlanCreated",
    b"EOS2": "OrderSubmitted",
    b"EOA2": "OrderAccepted",
    b"EOR2": "OrderRejected",
    b"EOC2": "OrderCanceled",
    b"EOX2": "OrderExpired",
    b"EFV2": "FillRecorded",
    b"EXV2": "ReconciliationRequired",
}


def decode_event(payload: bytes) -> Any:
    if len(payload) < 8:
        raise ValueError("Execution v2 event payload is truncated")
    root_name = _EVENT_ROOTS.get(payload[4:8])
    if root_name is None:
        raise ValueError("unknown Execution v2 event identifier")
    module = __import__(
        f"kairospy.infrastructure.transport.generated.kairos.execution.v2.{root_name}",
        fromlist=[root_name],
    )
    return getattr(module, root_name).GetRootAs(payload, 0)


__all__ = ["decode_event"]
