from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from kairospy.domain_types import AccountId

from .models import RiskStatus, RiskViolation


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
        event_sequence=_integer(root.get("event_sequence", 0), "event_sequence"),
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
