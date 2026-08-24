from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from kairospy.primitives.account import AccountId
from kairospy.investment.application.eventing import EventMetadata
from kairospy.primitives.time import datetime_from_unix_nanos

from .events import (
    ReservationChangedEvent,
    RiskCircuitChangedEvent,
    RiskDecisionEvent,
    RiskEvent,
    RiskEventRecord,
)
from .models import (
    ReservationChange,
    RiskCircuitChange,
    RiskDecisionChange,
    RiskStatus,
    RiskViolation,
)


def map_risk_event(record: RiskEventRecord) -> RiskEvent | None:
    occurred_at = datetime_from_unix_nanos(record.occurred_at_unix_nanos)
    metadata = EventMetadata(
        stream_id=record.stream_id,
        sequence=record.sequence,
        producer=record.producer,
        occurred_at=occurred_at,
        occurred_at_unix_nanos=record.occurred_at_unix_nanos,
    )
    payload = _mapping(record.payload, f"Risk {record.kind} event")
    if record.kind == "reservation_changed":
        if record.account_id is None or record.strategy_id is None:
            raise ValueError(
                "Risk reservation event requires account and strategy scope"
            )
        return ReservationChangedEvent(
            ReservationChange(
                reservation_id=str(payload["reservation_id"]),
                request_id=str(payload["request_id"]),
                account_id=AccountId(record.account_id),
                strategy_id=record.strategy_id,
                status=str(payload["status"]),
                occurred_at_unix_nanos=record.occurred_at_unix_nanos,
            ),
            metadata,
        )
    if record.kind == "decision_evaluated":
        if record.account_id is None or record.strategy_id is None:
            raise ValueError("Risk decision event requires account and strategy scope")
        return RiskDecisionEvent(
            RiskDecisionChange(
                decision_id=str(payload["decision_id"]),
                request_id=str(payload["request_id"]),
                account_id=AccountId(record.account_id),
                strategy_id=record.strategy_id,
                allowed=bool(payload.get("allowed", False)),
                degraded=bool(payload.get("degraded", False)),
                reason_codes=tuple(
                    str(value)
                    for value in _sequence(
                        payload.get("reason_codes", ()), "reason_codes"
                    )
                ),
                violations=tuple(
                    str(value)
                    for value in _sequence(payload.get("violations", ()), "violations")
                ),
                occurred_at_unix_nanos=record.occurred_at_unix_nanos,
            ),
            metadata,
        )
    if record.kind == "circuit_changed":
        return RiskCircuitChangedEvent(
            RiskCircuitChange(
                account_id=None
                if record.account_id is None
                else AccountId(record.account_id),
                strategy_id=record.strategy_id,
                exchange_id=_optional_text(payload.get("exchange_id")),
                open=str(payload.get("state", "")).lower() == "open",
                reason=str(payload.get("reason", "")),
                opened_at_unix_nanos=_optional_integer(
                    payload.get("opened_at_unix_nanos")
                ),
                reset_at_unix_nanos=_optional_integer(
                    payload.get("reset_at_unix_nanos")
                ),
                occurred_at_unix_nanos=record.occurred_at_unix_nanos,
            ),
            metadata,
        )
    if record.kind == "policy_activated":
        return None
    raise ValueError(f"unsupported Risk event kind: {record.kind}")


def map_risk_status(value: object, *, account_id: AccountId) -> RiskStatus:
    root = _mapping(value, "Risk status")
    root = _mapping(root.get("risk", root.get("status", root)), "Risk status payload")
    violations = tuple(
        RiskViolation(
            code=str(item.get("code", "unknown")),
            message=str(item.get("message", "")),
            limit=_decimal(item.get("limit")),
            actual=_decimal(item.get("actual")),
        )
        for item in (
            _mapping(raw, "risk violation")
            for raw in _sequence(root.get("violations", ()), "violations")
        )
    )
    return RiskStatus(
        account_id=account_id,
        trading_allowed=bool(root.get("trading_allowed", not violations)),
        available_notional=_decimal(root.get("available_notional")),
        reserved_notional=_decimal(root.get("reserved_notional")) or Decimal("0"),
        utilization=_decimal(root.get("utilization")),
        violations=violations,
        generation=_integer(root.get("generation", 0), "generation"),
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("decimal values must use the canonical string representation")
    return Decimal(value)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_integer(value: object) -> int | None:
    if value in (None, 0):
        return None
    return _integer(value, "optional integer")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None
