from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from kairospy.application.risk.events import RiskEventRecord
from kairospy.infrastructure.contracts.risk import decode_event
from kairospy.infrastructure.transport.native_event import NativeEventSource
from kairospy.infrastructure.transport.generated_spec import DEFAULT_CHANNEL, RISK_EVENTS


class AeronRiskEventSource(NativeEventSource[RiskEventRecord]):
    """Risk-owned subprocess adapter over the native Aeron subscription."""

    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = DEFAULT_CHANNEL,
        stream_id: int = RISK_EVENTS,
    ) -> None:
        super().__init__(
            decoder=decode_risk_event,
            aeron_dir=aeron_dir, channel=channel, stream_id=stream_id,
        )


def decode_risk_event(payload: bytes) -> RiskEventRecord:
    root = cast(Any, decode_event(payload))
    metadata = root.Metadata()
    if metadata is None:
        raise ValueError("Risk event metadata is missing")
    sequence = int(metadata.Sequence())
    if sequence <= 0:
        raise ValueError("Risk event sequence must be positive")
    occurred_at = int(metadata.OccurredAtUnixNanos())
    root_name = type(root).__name__
    if root_name.startswith("Reservation"):
        kind = "reservation_changed"
        reservation = root.Reservation()
        if reservation is None:
            raise ValueError("Risk reservation event is missing reservation")
        account_id = _required_text(reservation.AccountId(), "reservation.account_id")
        strategy_id = _required_text(reservation.StrategyId(), "reservation.strategy_id")
        body: object = {
            "reservation_id": _required_text(reservation.ReservationId(), "reservation_id"),
            "request_id": _required_text(reservation.RequestId(), "request_id"),
            "status": _reservation_status(reservation.Status()),
        }
    elif root_name == "RiskDecisionMade":
        kind = "decision_evaluated"
        decision = root.Decision()
        if decision is None:
            raise ValueError("Risk decision event is missing decision")
        account_id = _required_text(decision.AccountId(), "decision.account_id")
        strategy_id = _required_text(decision.StrategyId(), "decision.strategy_id")
        reasons = tuple(
            decision.Reasons(index)
            for index in range(decision.ReasonsLength())
        )
        body = {
            "decision_id": _required_text(decision.DecisionId(), "decision_id"),
            "request_id": _required_text(decision.RequestId(), "request_id"),
            "allowed": int(decision.Outcome()) in {1, 2},
            "degraded": int(decision.Outcome()) == 2,
            "reason_codes": tuple(_reason_code(reason.Code()) for reason in reasons),
            "violations": tuple(
                _optional_text(reason.Detail()) or ""
                for reason in reasons
                if reason.Detail() is not None
            ),
        }
    elif root_name in {"CircuitOpened", "CircuitClosed"}:
        kind = "circuit_changed"
        circuit = root.Circuit()
        if circuit is None:
            raise ValueError("Risk circuit event is missing circuit state")
        scope = circuit.Scope()
        account_id = None if scope is None else _optional_text(scope.AccountId())
        strategy_id = None if scope is None else _optional_text(scope.StrategyId())
        body = {
            "exchange_id": None if scope is None else _optional_text(scope.ExchangeId()),
            "state": "open" if root_name == "CircuitOpened" else "closed",
            "reason": _required_text(circuit.Reason(), "circuit.reason"),
            "opened_at_unix_nanos": _optional_int(circuit.OpenedAtUnixNanos()),
            "reset_at_unix_nanos": _optional_int(circuit.ResetAtUnixNanos()),
        }
    else:
        raise ValueError(f"unsupported Risk v2 event root: {root_name}")
    return RiskEventRecord(
        stream_id=_required_text(metadata.StreamId(), "stream_id"),
        sequence=sequence,
        producer=_required_text(metadata.ProducerId(), "producer_id"),
        kind=kind,
        account_id=account_id,
        strategy_id=strategy_id,
        payload=body,
        occurred_at_unix_nanos=occurred_at,
        launch_id=_optional_text(metadata.LaunchId()),
        instance_id=_optional_text(metadata.InstanceId()),
    )


def _required_text(value: bytes | None, name: str) -> str:
    result = "" if value is None else value.decode()
    if not result.strip():
        raise ValueError(f"Risk event {name} is required")
    return result


def _optional_text(value: bytes | None) -> str | None:
    if value is None:
        return None
    result = value.decode()
    return result if result.strip() else None


def _optional_int(value: int | None) -> int | None:
    return None if value is None else int(value)


def _reservation_status(value: int) -> str:
    return {
        1: "reserved",
        2: "consumed",
        3: "released",
        4: "expired",
    }.get(int(value), "unspecified")


def _reason_code(value: int) -> str:
    return {
        1: "no_matching_policy",
        2: "limit_exceeded",
        3: "stale_dependency",
        4: "duplicate_request",
        5: "reservation_not_found",
        6: "reservation_not_active",
        7: "invalid_request",
        8: "persistence_failure",
        9: "circuit_open",
        10: "stale_market",
        11: "insufficient_margin",
        12: "leverage_exceeded",
        13: "loss_limit_exceeded",
    }.get(int(value), "unspecified")


__all__ = ["AeronRiskEventSource", "RiskEventRecord", "decode_risk_event"]
