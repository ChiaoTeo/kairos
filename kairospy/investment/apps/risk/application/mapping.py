"""Consumer-owned mapping from the native Risk current snapshot."""

from __future__ import annotations

from collections.abc import Iterable

from kairospy.primitives.account import AccountId
from kairospy.primitives.decimal import Money, MoneyLike

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
    available = _sum_money(getattr(item, "available") for item in usages)
    reserved = _sum_money(getattr(item, "reserved") for item in usages)
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


def _sum_money(values: Iterable[object]) -> Money:
    total = Money("0")
    for value in values:
        if not isinstance(value, MoneyLike):
            raise ValueError("notional value is not a Risk contract Money")
        total = total.checked_add(value)
    return total
