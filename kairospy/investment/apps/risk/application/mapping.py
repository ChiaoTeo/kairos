"""Consumer-owned mapping from the native Risk current snapshot."""

from __future__ import annotations

from decimal import Decimal

from kairospy.primitives.account import AccountId

from .models import RiskStatus, RiskViolation


def map_risk_status(value: object, *, account_id: AccountId) -> RiskStatus:
    usages = tuple(
        item
        for item in getattr(value, "limits")
        if getattr(getattr(item, "policy"), "scope").account_id
        in {None, str(account_id)}
        and getattr(getattr(item, "policy"), "metric") == "notional"
    )
    circuits = tuple(
        item
        for item in getattr(value, "circuits")
        if getattr(item, "scope").account_id in {None, str(account_id)}
        and getattr(item, "status") == "open"
    )
    available = sum(
        (_decimal(getattr(item, "available")) for item in usages), Decimal("0")
    )
    reserved = sum(
        (_decimal(getattr(item, "reserved")) for item in usages), Decimal("0")
    )
    violations = tuple(
        RiskViolation(
            code="circuit_open",
            message=getattr(item, "reason") or "Risk circuit is open",
            limit=None,
            actual=None,
        )
        for item in circuits
    )
    return RiskStatus(
        account_id=account_id,
        trading_allowed=not violations,
        available_notional=available if usages else None,
        reserved_notional=reserved,
        utilization=None,
        violations=violations,
        generation=int(getattr(value, "applied_event_sequence")),
    )


def _decimal(value: object) -> Decimal:
    native = getattr(value, "value", None)
    if isinstance(native, Decimal):
        return native
    raise ValueError("decimal value is not a Risk contract decimal")
