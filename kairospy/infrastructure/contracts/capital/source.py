from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from kairospy.infrastructure.contracts.capital.records import CapitalEventRecord
from kairospy.infrastructure.contracts.capital import decode_event
from kairospy.infrastructure.protocol.generated_spec import CAPITAL_EVENTS, DEFAULT_CHANNEL
from kairospy.infrastructure.transport.native_event import NativeEventSource


class AeronCapitalEventSource(NativeEventSource[CapitalEventRecord]):
    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = DEFAULT_CHANNEL,
        stream_id: int = CAPITAL_EVENTS,
    ) -> None:
        super().__init__(
            decoder=decode_capital_event,
            aeron_dir=aeron_dir,
            channel=channel,
            stream_id=stream_id,
        )


def decode_capital_event(payload: bytes) -> CapitalEventRecord:
    root = cast(Any, decode_event(payload))
    metadata = root.Metadata()
    if metadata is None:
        raise ValueError("Capital event metadata is missing")
    sequence = int(metadata.Sequence())
    if sequence <= 0:
        raise ValueError("Capital event sequence must be positive")
    root_name = type(root).__name__
    kind = {
        "FundingObjectiveChanged": "funding_objective_changed",
        "CapitalDemandChanged": "capital_demand_changed",
        "CapitalPolicyChanged": "policy_changed",
        "CapitalFactsObserved": "facts_observed",
        "CapitalAvailabilityEvaluated": "availability_evaluated",
        "CapitalRouteChanged": "route_changed",
        "CapitalPlanAuthorized": "plan_authorized",
        "CapitalPlanStateChanged": "plan_state_changed",
        "CapitalPlanExpired": "plan_expired",
    }.get(root_name)
    if kind is None:
        raise ValueError(f"unsupported Capital v2 event root: {root_name}")
    return CapitalEventRecord(
        stream_id=_required_text(metadata.StreamId(), "stream_id"),
        sequence=sequence,
        producer=_required_text(metadata.ProducerId(), "producer_id"),
        kind=kind,
        payload=root,
        occurred_at_unix_nanos=int(metadata.OccurredAtUnixNanos()),
        launch_id=_optional_text(metadata.LaunchId()),
        instance_id=_optional_text(metadata.InstanceId()),
    )


def _required_text(value: bytes | None, name: str) -> str:
    result = "" if value is None else value.decode()
    if not result.strip():
        raise ValueError(f"Capital event {name} is required")
    return result


def _optional_text(value: bytes | None) -> str | None:
    if value is None:
        return None
    result = value.decode()
    return result if result.strip() else None


__all__ = ["AeronCapitalEventSource", "CapitalEventRecord", "decode_capital_event"]
