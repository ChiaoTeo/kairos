"""Consumer-owned mapping from the native Risk current snapshot."""

from __future__ import annotations

from collections.abc import Iterable

from kairospy.primitives.account import AccountId
from kairospy.contracts.risk.types import RiskCurrentSnapshot
from kairospy.primitives.decimal import DecimalValue, Money
from kairospy.primitives.time import Generation

from .models import RiskStatus, RiskViolation


def map_risk_status(value: RiskCurrentSnapshot, *, account_id: AccountId) -> RiskStatus:
    usages = tuple(
        item
        for item in value.limits
        if item.policy.scope.account_id
        in {None, str(account_id)}
        and item.policy.metric == "notional"
    )
    circuits = tuple(
        item
        for item in value.circuits
        if item.scope.account_id in {None, str(account_id)}
        and item.status == "open"
    )
    available = _sum_money(item.available for item in usages)
    reserved = _sum_money(item.reserved for item in usages)
    violations = tuple(
        RiskViolation(
            code="circuit_open",
            message=item.reason or "Risk circuit is open",
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
        generation=Generation(value.generation),
    )


def _sum_money(values: Iterable[DecimalValue]) -> Money:
    total = Money("0")
    for value in values:
        total = total.checked_add(Money(value.value))
    return total
