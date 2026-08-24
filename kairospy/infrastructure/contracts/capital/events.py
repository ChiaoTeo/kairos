"""Capital v2 typed event decoding into generated FlatBuffers roots."""

from __future__ import annotations

import sys
from typing import Any

from kairospy.infrastructure.protocol.generated import kairos as _generated_kairos

sys.modules.setdefault("kairos", _generated_kairos)


_EVENT_ROOTS: tuple[tuple[bytes, str], ...] = (
    (b"COV2", "FundingObjectiveChanged"),
    (b"CDV2", "CapitalDemandChanged"),
    (b"CYV2", "CapitalPolicyChanged"),
    (b"CFV2", "CapitalFactsObserved"),
    (b"CAV2", "CapitalAvailabilityEvaluated"),
    (b"CRV2", "CapitalRouteChanged"),
    (b"CPAV", "CapitalPlanAuthorized"),
    (b"CPSV", "CapitalPlanStateChanged"),
    (b"CPEV", "CapitalPlanExpired"),
)


def decode_event(payload: bytes) -> Any:
    for identifier, root_name in _EVENT_ROOTS:
        if len(payload) < 8 or payload[4:8] != identifier:
            continue
        module = __import__(
            f"kairospy.infrastructure.protocol.generated.kairos.capital.v2.{root_name}",
            fromlist=[root_name],
        )
        return getattr(module, root_name).GetRootAs(payload, 0)
    raise ValueError("unknown Capital v2 event identifier")


__all__ = ["decode_event"]
